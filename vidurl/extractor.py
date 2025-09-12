"""
Main video extraction functionality.
"""

import os
import sys
import time
import re
import subprocess
import logging
import json
from urllib.parse import urljoin, urlparse
from typing import Optional, Set, List, Dict, Union, Any
from concurrent.futures import ThreadPoolExecutor, as_completed

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.common.exceptions import WebDriverException
from bs4 import BeautifulSoup

from .config import VideoExtractorConfig
from .utils import setup_logging, retry_with_backoff
from .exceptions import (
    VideoExtractorError,
    WebDriverSetupError,
    VideoNotFoundError,
    VideoValidationError,
    NetworkError
)


class VideoExtractor:
    def __init__(self, config: VideoExtractorConfig = None):
        """Initialize VideoExtractor with configuration."""
        self.config = config or VideoExtractorConfig()
        self.driver = None
        self.logger = logging.getLogger(__name__)
        setup_logging(self.config)
        self.setup_driver()
    
    def setup_driver(self) -> None:
        """Setup Chrome WebDriver with appropriate options based on configuration."""
        try:
            chrome_options = Options()
            
            if self.config.headless:
                chrome_options.add_argument('--headless')
            
            chrome_options.add_argument('--no-sandbox')
            chrome_options.add_argument('--disable-dev-shm-usage')
            chrome_options.add_argument('--disable-gpu')
            chrome_options.add_argument(f'--window-size={self.config.window_size}')
            chrome_options.add_argument(f'--user-agent={self.config.user_agent}')
            
            # Enable logging for network requests
            chrome_options.add_argument('--enable-logging')
            chrome_options.add_argument('--log-level=0')
            chrome_options.set_capability('goog:loggingPrefs', {'performance': 'ALL'})
            
            self.driver = webdriver.Chrome(options=chrome_options)
            self.driver.set_page_load_timeout(self.config.page_load_timeout)
            
            self.logger.info("Chrome WebDriver initialized successfully")
            
        except WebDriverException as e:
            error_msg = f"Error setting up Chrome driver: {e}"
            self.logger.error(error_msg)
            self.logger.error("Make sure ChromeDriver is installed and in PATH")
            raise WebDriverSetupError(error_msg) from e
    
    def find_main_video(self, url: str) -> Optional[str]:
        """Find the main video URL from the webpage and return validated curl command."""
        try:
            self.logger.info(f"Loading page: {url}")
            self.driver.get(url)
            
            # Wait for page to load
            time.sleep(3)
            
            # Get page source after JavaScript execution
            soup = BeautifulSoup(self.driver.page_source, 'html.parser')
            
            video_urls: Set[str] = set()
            
            # Method 1: Find HTML5 video elements
            self.logger.debug("Searching for HTML5 video elements")
            video_urls.update(self.find_html5_videos(soup, url))
            
            # Method 2: Find video URLs in script tags
            self.logger.debug("Searching for video URLs in script tags")
            video_urls.update(self.find_videos_in_scripts(soup, url))
            
            # Method 3: Check initial network requests
            self.logger.debug("Checking network requests for video URLs")
            video_urls.update(self.find_videos_in_network())
            
            # Method 4: Try to find and click play buttons to trigger video loading
            self.logger.debug("Triggering video loading and monitoring network")
            new_video_urls = self.trigger_video_loading_and_monitor()
            video_urls.update(new_video_urls)
            
            # Method 5: Look for common video hosting patterns
            self.logger.debug("Searching for embedded video patterns")
            video_urls.update(self.find_embedded_videos(soup, url))
            
            if not video_urls:
                self.logger.warning("No video URLs found")
                raise VideoNotFoundError("No video URLs found on the page")
            
            self.logger.info(f"Found {len(video_urls)} video URL(s)")
            
            # Validate URLs in parallel for better performance
            validated_command = self._validate_urls_parallel(list(video_urls), url)
            
            if validated_command:
                return validated_command
            
            self.logger.warning("No valid video URLs found after validation")
            raise VideoValidationError("No valid video URLs found after validation")
            
        except (VideoNotFoundError, VideoValidationError):
            raise
        except Exception as e:
            self.logger.error(f"Error extracting videos: {e}")
            raise VideoExtractorError(f"Error extracting videos: {e}") from e

    def _validate_urls_parallel(self, video_urls: List[str], original_url: str) -> Optional[str]:
        """Validate multiple video URLs in parallel and return the first valid curl command."""
        if not video_urls:
            return None
        
        self.logger.info(f"Validating {len(video_urls)} video URLs in parallel")
        
        with ThreadPoolExecutor(max_workers=self.config.max_workers) as executor:
            # Submit all validation tasks
            future_to_url = {
                executor.submit(self.get_download_command, url, original_url): url 
                for url in video_urls
            }
            
            # Return the first successful validation
            for future in as_completed(future_to_url):
                url = future_to_url[future]
                try:
                    result = future.result()
                    if result:
                        self.logger.info(f"Successfully validated video URL: {url}")
                        # Cancel remaining tasks
                        for remaining_future in future_to_url:
                            if remaining_future != future:
                                remaining_future.cancel()
                        return result
                except Exception as e:
                    self.logger.debug(f"Validation failed for {url}: {e}")
                    continue
        
        return None
    
    @retry_with_backoff(max_retries=3, backoff_factor=2.0, exceptions=(subprocess.SubprocessError, NetworkError))
    def get_download_command(self, video_url: str, original_url: Optional[str] = None) -> Optional[str]:
        """Validate video URL with actual data streaming test and return curl command."""
        try:
            # Determine output filename from original URL if provided, fallback to video URL
            url_for_filename = original_url if original_url else video_url
            parsed_url = urlparse(url_for_filename)
            filename = os.path.basename(parsed_url.path)
            if not filename or '.' not in filename:
                filename = self.config.default_filename
            
            # Add output directory if specified
            if self.config.output_dir != ".":
                filename = os.path.join(self.config.output_dir, filename)
            
            self.logger.debug(f"Validating video URL with streaming test: {video_url}")
            
            # Get cookies from the browser session
            cookies = self.driver.get_cookies()
            
            # Create cookie string for curl
            cookie_string = "; ".join([f"{cookie['name']}={cookie['value']}" for cookie in cookies])
            
            # Build curl command for partial content request to validate streaming
            test_cmd = [
                'curl',
                '-L',  # Follow redirects
                '-s',  # Silent mode
                '--max-time', str(self.config.curl_timeout),
                '-H', f'Range: bytes=0-{self.config.validation_chunk_size - 1}',  # Request validation chunk
                '-H', f'User-Agent: {self.config.user_agent}',
                '-H', f'Referer: {self.driver.current_url}',
                '-H', 'Accept: video/webm,video/ogg,video/*;q=0.9,application/ogg;q=0.7,audio/*;q=0.6,*/*;q=0.5',
                '-H', 'Accept-Language: en-US,en;q=0.9',
                '-H', 'Connection: keep-alive'
            ]
            
            # Add cookies if available
            if cookie_string:
                test_cmd.extend(['-H', f'Cookie: {cookie_string}'])
            
            test_cmd.extend(['-w', '%{http_code},%{size_download}', '-o', '/dev/null', video_url])
            
            # Execute streaming test
            result = subprocess.run(test_cmd, capture_output=True, text=True, timeout=self.config.curl_timeout + 5)
            
            if result.returncode != 0:
                error_msg = f"Streaming test failed with return code: {result.returncode}"
                self.logger.debug(f"{error_msg}. stderr: {result.stderr}")
                raise NetworkError(error_msg)
            
            # Parse curl output (status_code,bytes_downloaded)
            output_parts = result.stdout.strip().split(',')
            if len(output_parts) != 2:
                error_msg = f"Unexpected curl output format: {result.stdout}"
                self.logger.debug(error_msg)
                raise VideoValidationError(error_msg)
                
            status_code, bytes_downloaded = output_parts
            bytes_downloaded = int(bytes_downloaded) if bytes_downloaded.isdigit() else 0
            
            # Validate streaming success
            if status_code not in ['200', '206']:
                error_msg = f"Invalid HTTP status code: {status_code}"
                self.logger.debug(error_msg)
                raise VideoValidationError(error_msg)
                
            if bytes_downloaded < self.config.min_download_size:
                error_msg = f"Insufficient data downloaded: {bytes_downloaded} bytes"
                self.logger.debug(error_msg)
                raise VideoValidationError(error_msg)
                
            self.logger.info(f"✓ Video streaming validated: {bytes_downloaded} bytes downloaded with status {status_code}")
            
            # Get additional info with HEAD request for file size
            head_cmd = [
                'curl', '-I', '-L', '-s', '--max-time', str(self.config.curl_timeout),
                '-H', f'User-Agent: {self.config.user_agent}',
                '-H', f'Referer: {self.driver.current_url}'
            ]
            
            if cookie_string:
                head_cmd.extend(['-H', f'Cookie: {cookie_string}'])
            head_cmd.append(video_url)
            
            try:
                head_result = subprocess.run(head_cmd, capture_output=True, text=True, timeout=self.config.curl_timeout + 2)
                if head_result.returncode == 0:
                    headers = head_result.stdout
                    # Show file size if available
                    for line in headers.split('\\n'):
                        if line.lower().startswith('content-length:'):
                            try:
                                content_length = line.split(':', 1)[1].strip()
                                size_mb = int(content_length) / (1024 * 1024)
                                self.logger.info(f"Total video file size: {size_mb:.2f} MB")
                            except (ValueError, IndexError):
                                pass
            except subprocess.TimeoutExpired:
                self.logger.debug("HEAD request timed out, but validation was successful")
            
            self.logger.info("✓ Video URL validated successfully with actual streaming test")
            
            # Build final download command with validated parameters from successful streaming
            download_cmd = [
                'curl',
                '-L',  # Follow redirects
                '--progress-bar',  # Show progress bar
                '-o', filename,  # Output file
                '-H', f'User-Agent: {self.config.user_agent}',
                '-H', f'Referer: {self.driver.current_url}',
                '-H', 'Accept: video/webm,video/ogg,video/*;q=0.9,application/ogg;q=0.7,audio/*;q=0.6,*/*;q=0.5',
                '-H', 'Accept-Language: en-US,en;q=0.9',
                '-H', 'Accept-Encoding: gzip, deflate, br',
                '-H', 'Connection: keep-alive',
                '-H', 'Upgrade-Insecure-Requests: 1',
                '-H', 'Sec-Fetch-Dest: video',
                '-H', 'Sec-Fetch-Mode: no-cors',
                '-H', 'Sec-Fetch-Site: same-origin',
                '-H', 'Cache-Control: no-cache',
                '-H', 'Pragma: no-cache'
            ]
            
            # Add cookies from validated browser session
            if cookie_string:
                download_cmd.extend(['-H', f'Cookie: {cookie_string}'])
            
            download_cmd.append(video_url)
            
            return ' '.join(f'"{arg}"' if ' ' in arg or arg == video_url else arg for arg in download_cmd)
            
        except subprocess.TimeoutExpired as e:
            error_msg = f"Curl validation timed out after {self.config.curl_timeout} seconds"
            self.logger.debug(error_msg)
            raise NetworkError(error_msg) from e
        except subprocess.SubprocessError as e:
            error_msg = f"Error executing curl: {e}"
            self.logger.debug(f"{error_msg}. Make sure curl is installed and available in PATH")
            raise NetworkError(error_msg) from e
        except (VideoValidationError, NetworkError):
            raise
        except Exception as e:
            error_msg = f"Unexpected error during validation: {e}"
            self.logger.debug(error_msg)
            raise VideoExtractorError(error_msg) from e
    
    
    def trigger_video_loading_and_monitor(self) -> Set[str]:
        """Try to trigger video loading by clicking play buttons and monitor network requests."""
        video_urls: Set[str] = set()
        
        # Enhanced play button selectors
        play_selectors = [
            'button[aria-label*="play" i]',
            'button[title*="play" i]',
            '.play-button',
            '.video-play-button',
            '[data-action="play"]',
            '[data-role="play"]',
            'button.play',
            '.play-btn',
            '[class*="play"]',
            'button[class*="play" i]',
            '.vjs-big-play-button',  # Video.js
            '.plyr__control--overlaid',  # Plyr
            '.jwplayer .jw-display-icon-display',  # JW Player
        ]
        
        for selector in play_selectors:
            try:
                # Clear previous network logs
                self.driver.get_log('performance')
                
                elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                for element in elements[:3]:  # Try up to 3 elements
                    try:
                        if element.is_displayed() and element.is_enabled():
                            self.logger.debug(f"Clicking play button: {selector}")
                            self.driver.execute_script("arguments[0].click();", element)
                            
                            # Wait a bit for video loading
                            time.sleep(2)
                            
                            # Check network logs for new video requests
                            new_videos = self.find_videos_in_network()
                            if new_videos:
                                self.logger.info(f"Found {len(new_videos)} video URLs after clicking {selector}")
                                video_urls.update(new_videos)
                                return video_urls  # Return first successful result
                                
                    except Exception as click_error:
                        self.logger.debug(f"Failed to click element with {selector}: {click_error}")
                        continue
                        
            except Exception as selector_error:
                self.logger.debug(f"Error with selector {selector}: {selector_error}")
                continue
        
        return video_urls
    
    def find_html5_videos(self, soup, base_url: str) -> Set[str]:
        """Find HTML5 video elements and their sources."""
        video_urls: Set[str] = set()
        
        # Find video tags with src attribute
        for video in soup.find_all('video', src=True):
            video_url = urljoin(base_url, video['src'])
            video_urls.add(video_url)
            self.logger.debug(f"Found HTML5 video: {video_url}")
        
        # Find source tags within video elements
        for source in soup.find_all('source', src=True):
            video_url = urljoin(base_url, source['src'])
            video_urls.add(video_url)
            self.logger.debug(f"Found HTML5 source: {video_url}")
        
        return video_urls
    
    def find_videos_in_scripts(self, soup, base_url: str) -> Set[str]:
        """Find video URLs in JavaScript code."""
        video_urls: Set[str] = set()
        
        # Enhanced video URL patterns including streaming protocols
        video_patterns = [
            r'https?://[^"\s]+\.(?:mp4|webm|ogg|avi|mov|wmv|flv|m4v|mkv)',
            r'https?://[^"\s]+\.m3u8(?:\?[^"\s]*)?',  # HLS streams
            r'https?://[^"\s]+\.mpd(?:\?[^"\s]*)?',   # DASH streams
            r'"(?:video_url|videoUrl|src|source)"\s*:\s*"([^"]+)"',
            r'file\s*:\s*["\']([^"\']+)["\']',
            r'source\s*:\s*["\']([^"\']+)["\']',
            r'mp4\s*:\s*["\']([^"\']+)["\']',
            r'hls\s*:\s*["\']([^"\']+)["\']',
        ]
        
        for script in soup.find_all('script'):
            if script.string:
                for pattern in video_patterns:
                    matches = re.findall(pattern, script.string, re.IGNORECASE)
                    for match in matches:
                        # Handle tuple results from capture groups
                        url = match if isinstance(match, str) else match[0] if match else ""
                        if url:
                            if url.startswith('http'):
                                video_urls.add(url)
                            else:
                                # Convert relative URLs to absolute
                                absolute_url = urljoin(base_url, url)
                                video_urls.add(absolute_url)
                            self.logger.debug(f"Found video URL in script: {url}")
        
        return video_urls
    
    def find_videos_in_network(self) -> Set[str]:
        """Check browser network logs for successfully streamed video requests."""
        video_urls: Set[str] = set()
        
        try:
            import json
            # Get network logs (requires Chrome with logging enabled)
            logs = self.driver.get_log('performance')
            
            # Track request-response pairs to validate successful streaming
            request_map: Dict[str, Dict[str, Any]] = {}
            successful_video_urls: Set[str] = set()
            
            for log in logs:
                try:
                    message = log.get('message', {})
                    if isinstance(message, str):
                        message = json.loads(message)
                    
                    method = message.get('message', {}).get('method')
                    
                    # Track outgoing video requests
                    if method == 'Network.requestWillBeSent':
                        request = message['message']['params']['request']
                        request_id = message['message']['params']['requestId']
                        url = request.get('url', '')
                        
                        # Check for video file extensions
                        if any(ext in url.lower() for ext in self.config.video_extensions):
                            request_map[request_id] = {'url': url, 'type': 'extension'}
                            self.logger.debug(f"Found video request by extension: {url}")
                        
                        # Check for streaming segments
                        elif any(segment in url.lower() for segment in self.config.streaming_segments):
                            request_map[request_id] = {'url': url, 'type': 'streaming'}
                            self.logger.debug(f"Found streaming request: {url}")
                    
                    # Validate successful video responses
                    elif method == 'Network.responseReceived':
                        request_id = message['message']['params']['requestId']
                        response = message['message']['params']['response']
                        url = response.get('url', '')
                        mime_type = response.get('mimeType', '').lower()
                        headers = response.get('headers', {})
                        status = response.get('status', 0)
                        
                        # Check if this is a video response with successful status
                        if status in [200, 206]:  # 206 for partial content (streaming)
                            is_video = False
                            
                            # Check MIME type first (most reliable)
                            if 'video/' in mime_type:
                                is_video = True
                                self.logger.debug(f"Found video response by MIME type ({mime_type}): {url}")
                            
                            # Check content-type header
                            elif any('video/' in str(v).lower() for v in headers.values()):
                                is_video = True
                                self.logger.debug(f"Found video response by content-type header: {url}")
                            
                            # Check if this matches a tracked video request
                            elif request_id in request_map:
                                is_video = True
                                self.logger.debug(f"Found successful video response for tracked request: {url}")
                            
                            if is_video:
                                successful_video_urls.add(url)
                    
                    # Track data received to confirm actual streaming
                    elif method == 'Network.dataReceived':
                        request_id = message['message']['params']['requestId']
                        data_length = message['message']['params']['dataLength']
                        
                        # If this is a tracked video request with substantial data, mark as successful
                        if request_id in request_map and data_length > self.config.min_download_size:
                            url = request_map[request_id]['url']
                            successful_video_urls.add(url)
                            self.logger.debug(f"Confirmed video streaming with {data_length} bytes received: {url}")
                
                except (json.JSONDecodeError, KeyError, TypeError) as e:
                    continue
            
            video_urls = successful_video_urls
            self.logger.info(f"Network monitoring found {len(video_urls)} video URLs")
                    
        except Exception as e:
            self.logger.error(f"Error reading network logs: {e}")
        
        return video_urls
    
    def find_embedded_videos(self, soup, base_url: str) -> Set[str]:
        """Find embedded videos from common platforms."""
        video_urls: Set[str] = set()
        
        # Enhanced platform detection patterns
        platform_patterns = {
            'youtube': [
                r'youtube\.com/embed/([a-zA-Z0-9_-]+)',
                r'youtube\.com/watch\?v=([a-zA-Z0-9_-]+)',
                r'youtu\.be/([a-zA-Z0-9_-]+)',
            ],
            'vimeo': [
                r'vimeo\.com/([0-9]+)',
                r'player\.vimeo\.com/video/([0-9]+)',
            ],
            'dailymotion': [
                r'dailymotion\.com/embed/video/([a-zA-Z0-9]+)',
                r'dailymotion\.com/video/([a-zA-Z0-9]+)',
            ],
            'twitch': [
                r'twitch\.tv/videos/([0-9]+)',
                r'clips\.twitch\.tv/([a-zA-Z0-9_-]+)',
            ]
        }
        
        # Check iframe sources
        for iframe in soup.find_all('iframe', src=True):
            src = iframe['src']
            full_url = urljoin(base_url, src)
            
            for platform, patterns in platform_patterns.items():
                for pattern in patterns:
                    if re.search(pattern, full_url, re.IGNORECASE):
                        video_urls.add(full_url)
                        self.logger.debug(f"Found {platform} embedded video: {full_url}")
                        break
        
        # Check for data attributes that might contain video URLs
        for element in soup.find_all(['div', 'span', 'section'], attrs={'data-src': True}):
            data_src = element['data-src']
            if any(ext in data_src.lower() for ext in self.config.video_extensions):
                full_url = urljoin(base_url, data_src)
                video_urls.add(full_url)
                self.logger.debug(f"Found data-src video: {full_url}")
        
        # Check for video poster attributes (sometimes contain actual video URLs)
        for video in soup.find_all('video', attrs={'poster': True}):
            poster_url = video['poster']
            # Sometimes poster URLs point to video thumbnails with predictable video URLs
            if poster_url.endswith(('.jpg', '.png', '.jpeg')):
                # Try common video extensions
                for ext in ['.mp4', '.webm']:
                    video_candidate = poster_url.rsplit('.', 1)[0] + ext
                    full_url = urljoin(base_url, video_candidate)
                    video_urls.add(full_url)
                    self.logger.debug(f"Inferred video URL from poster: {full_url}")
        
        return video_urls
    
    def close(self) -> None:
        """Close the browser driver and clean up resources."""
        if self.driver:
            try:
                self.driver.quit()
                self.logger.debug("WebDriver closed successfully")
            except Exception as e:
                self.logger.warning(f"Error closing WebDriver: {e}")
            finally:
                self.driver = None

    def __enter__(self):
        """Context manager entry."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit with cleanup."""
        self.close()
        if exc_type is not None:
            self.logger.error(f"Exception occurred: {exc_type.__name__}: {exc_val}")
        return False  # Don't suppress exceptions