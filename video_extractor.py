#!/usr/bin/env python3
"""
Video URL Extractor

Extracts the main video URL from a web page and returns a validated curl command.
"""

import sys
import time
import re
import os
import subprocess
from urllib.parse import urljoin, urlparse
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.common.exceptions import WebDriverException
from bs4 import BeautifulSoup


class VideoExtractor:
    def __init__(self):
        self.driver = None
        self.setup_driver()
    
    def setup_driver(self):
        """Setup Chrome WebDriver with appropriate options"""
        chrome_options = Options()
        chrome_options.add_argument('--headless')
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--disable-gpu')
        chrome_options.add_argument('--window-size=1920,1080')
        chrome_options.add_argument('--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36')
        
        # Enable logging for network requests
        chrome_options.add_argument('--enable-logging')
        chrome_options.add_argument('--log-level=0')
        chrome_options.set_capability('goog:loggingPrefs', {'performance': 'ALL'})
        
        try:
            self.driver = webdriver.Chrome(options=chrome_options)
        except WebDriverException as e:
            print(f"Error setting up Chrome driver: {e}")
            print("Make sure ChromeDriver is installed and in PATH")
            sys.exit(1)
    
    def find_main_video(self, url):
        """Find the main video URL from the webpage and return validated curl command"""
        try:
            print(f"Loading page: {url}")
            self.driver.get(url)
            
            # Wait for page to load
            time.sleep(3)
            
            # Get page source after JavaScript execution
            soup = BeautifulSoup(self.driver.page_source, 'html.parser')
            
            video_urls = set()
            
            # Method 1: Find HTML5 video elements
            video_urls.update(self.find_html5_videos(soup, url))
            
            # Method 2: Find video URLs in script tags
            video_urls.update(self.find_videos_in_scripts(soup, url))
            
            # Method 3: Check initial network requests
            video_urls.update(self.find_videos_in_network())
            
            # Method 4: Try to find and click play buttons to trigger video loading
            new_video_urls = self.trigger_video_loading_and_monitor()
            video_urls.update(new_video_urls)
            
            # Method 5: Look for common video hosting patterns
            video_urls.update(self.find_embedded_videos(soup, url))
            
            if not video_urls:
                print("No video URLs found.")
                return None
            
            print(f"Found {len(video_urls)} video URL(s)")
            
            # Try each video URL until we find one that works
            for video_url in video_urls:
                print(f"Testing video URL: {video_url}")
                curl_command = self.get_download_command(video_url)
                if curl_command:
                    return curl_command
            
            print("No valid video URLs found.")
            return None
            
        except Exception as e:
            print(f"Error extracting videos: {e}")
            return None
    
    def get_download_command(self, video_url):
        """Validate video URL and return curl command for downloading"""
        try:
            # Determine output filename
            parsed_url = urlparse(video_url)
            filename = os.path.basename(parsed_url.path)
            if not filename or '.' not in filename:
                filename = 'video.mp4'
            
            print(f"Validating video URL: {video_url}")
            
            # Get cookies from the browser session
            cookies = self.driver.get_cookies()
            
            # Create cookie string for curl
            cookie_string = "; ".join([f"{cookie['name']}={cookie['value']}" for cookie in cookies])
            
            # Build curl command for HEAD request to validate
            head_cmd = [
                'curl',
                '-I',  # HEAD request only
                '-L',  # Follow redirects
                '-s',  # Silent mode
                '-H', 'User-Agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                '-H', f'Referer: {self.driver.current_url}',
                '-H', 'Accept: video/webm,video/ogg,video/*;q=0.9,application/ogg;q=0.7,audio/*;q=0.6,*/*;q=0.5',
                '-H', 'Accept-Language: en-US,en;q=0.9',
                '-H', 'Connection: keep-alive'
            ]
            
            # Add cookies if available
            if cookie_string:
                head_cmd.extend(['-H', f'Cookie: {cookie_string}'])
            
            head_cmd.append(video_url)
            
            # Execute HEAD request to validate
            result = subprocess.run(head_cmd, capture_output=True, text=True)
            
            if result.returncode != 0:
                print(f"HEAD request failed with return code: {result.returncode}")
                return None
            
            # Parse response headers
            headers = result.stdout
            status_line = headers.split('\n')[0] if headers else ''
            
            # Check status code
            if '200' not in status_line and '206' not in status_line:
                print(f"Invalid status code: {status_line}")
                return None
            
            # Check content type
            content_type = ''
            content_length = ''
            for line in headers.split('\n'):
                if line.lower().startswith('content-type:'):
                    content_type = line.split(':', 1)[1].strip().lower()
                elif line.lower().startswith('content-length:'):
                    content_length = line.split(':', 1)[1].strip()
            
            # Validate content type
            valid_types = ['video/', 'application/octet-stream', 'binary/octet-stream']
            if not any(vtype in content_type for vtype in valid_types) and content_type:
                print(f"Warning: Unexpected content type: {content_type}")
            
            # Show file size if available
            if content_length:
                try:
                    size_mb = int(content_length) / (1024 * 1024)
                    print(f"Video file size: {size_mb:.2f} MB")
                except:
                    pass
            
            print("✓ Video URL validated successfully")
            
            # Build final download command
            download_cmd = [
                'curl',
                '-L',  # Follow redirects
                '--progress-bar',  # Show progress bar
                '-o', filename,  # Output file
                '-H', 'User-Agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
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
            
            # Add cookies if available
            if cookie_string:
                download_cmd.extend(['-H', f'Cookie: {cookie_string}'])
            
            download_cmd.append(video_url)
            
            return ' '.join(f'"{arg}"' if ' ' in arg else arg for arg in download_cmd)
            
        except subprocess.SubprocessError as e:
            print(f"Error executing curl: {e}")
            print("Make sure curl is installed and available in PATH")
            return None
        except Exception as e:
            print(f"Unexpected error during validation: {e}")
            return None
    
    
    def trigger_video_loading_and_monitor(self):
        """Try to trigger video loading by clicking play buttons and monitor network requests"""
        video_urls = set()
        
        play_selectors = [
            'button[aria-label*="play" i]',
            'button[title*="play" i]',
            '.play-button',
            '.video-play-button',
            '[class*="play"]',
            'button:contains("Play")',
            '.vjs-big-play-button',
            'video',  # Try clicking on video elements directly
            '.video-container',
            '[data-testid*="play"]',
            '[role="button"]'
        ]
        
        print("Looking for play buttons and monitoring network requests...")
        
        for selector in play_selectors:
            try:
                elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                for element in elements:
                    if element.is_displayed():
                        print(f"Clicking element: {selector}")
                        
                        # Clear previous logs
                        self.driver.get_log('performance')
                        
                        # Click the element
                        element.click()
                        
                        # Wait and monitor network requests
                        for i in range(5):  # Monitor for 5 seconds
                            time.sleep(1)
                            new_urls = self.find_videos_in_network()
                            if new_urls:
                                video_urls.update(new_urls)
                                print(f"Found {len(new_urls)} video URLs after clicking")
                        
                        # Try different click methods if first didn't work
                        try:
                            self.driver.execute_script("arguments[0].click();", element)
                            time.sleep(2)
                            new_urls = self.find_videos_in_network()
                            video_urls.update(new_urls)
                        except:
                            pass
                        
                        if video_urls:
                            break
            except Exception as e:
                print(f"Error with selector {selector}: {e}")
                continue
        
        return video_urls
    
    def find_html5_videos(self, soup, base_url):
        """Find HTML5 video elements"""
        video_urls = set()
        
        # Find video tags
        for video in soup.find_all('video'):
            # Check src attribute
            if video.get('src'):
                video_urls.add(urljoin(base_url, video['src']))
            
            # Check source children
            for source in video.find_all('source'):
                if source.get('src'):
                    video_urls.add(urljoin(base_url, source['src']))
        
        return video_urls
    
    def find_videos_in_scripts(self, soup, base_url):
        """Find video URLs in JavaScript code"""
        video_urls = set()
        
        # Common video URL patterns
        video_patterns = [
            r'"(https?://[^"]*\.(?:mp4|webm|ogg|avi|mov|wmv|flv|m4v)(?:\?[^"]*)?)"',
            r"'(https?://[^']*\.(?:mp4|webm|ogg|avi|mov|wmv|flv|m4v)(?:\?[^']*)?)'",
            r'src["\s]*:["\s]*(https?://[^"\']*\.(?:mp4|webm|ogg|avi|mov|wmv|flv|m4v)(?:\?[^"\']*)?)',
            r'url["\s]*:["\s]*(https?://[^"\']*\.(?:mp4|webm|ogg|avi|mov|wmv|flv|m4v)(?:\?[^"\']*)?)',
            r'file["\s]*:["\s]*(https?://[^"\']*\.(?:mp4|webm|ogg|avi|mov|wmv|flv|m4v)(?:\?[^"\']*)?)'
        ]
        
        for script in soup.find_all('script'):
            if script.string:
                for pattern in video_patterns:
                    matches = re.findall(pattern, script.string, re.IGNORECASE)
                    for match in matches:
                        video_urls.add(match)
        
        return video_urls
    
    def find_videos_in_network(self):
        """Check browser network logs for video requests"""
        video_urls = set()
        
        try:
            import json
            # Get network logs (requires Chrome with logging enabled)
            logs = self.driver.get_log('performance')
            
            for log in logs:
                try:
                    message = log.get('message', {})
                    if isinstance(message, str):
                        message = json.loads(message)
                    
                    # Check for network responses
                    if message.get('message', {}).get('method') == 'Network.responseReceived':
                        response = message['message']['params']['response']
                        url = response.get('url', '')
                        mime_type = response.get('mimeType', '').lower()
                        headers = response.get('headers', {})
                        
                        # Check for video file extensions
                        video_extensions = ['.mp4', '.webm', '.ogg', '.avi', '.mov', '.wmv', '.flv', '.m4v', '.mkv']
                        if any(ext in url.lower() for ext in video_extensions):
                            print(f"Found video URL by extension: {url}")
                            video_urls.add(url)
                        
                        # Check MIME type
                        elif 'video/' in mime_type:
                            print(f"Found video URL by MIME type ({mime_type}): {url}")
                            video_urls.add(url)
                        
                        # Check content-type header
                        elif any('video/' in str(v).lower() for v in headers.values()):
                            print(f"Found video URL by content-type header: {url}")
                            video_urls.add(url)
                        
                        # Check for streaming segments (HLS, DASH)
                        elif any(segment in url.lower() for segment in ['.m3u8', '.mpd', '/segment', '/chunk']):
                            print(f"Found streaming URL: {url}")
                            video_urls.add(url)
                    
                    # Also check for network requests (not just responses)
                    elif message.get('message', {}).get('method') == 'Network.requestWillBeSent':
                        request = message['message']['params']['request']
                        url = request.get('url', '')
                        
                        video_extensions = ['.mp4', '.webm', '.ogg', '.avi', '.mov', '.wmv', '.flv', '.m4v', '.mkv']
                        if any(ext in url.lower() for ext in video_extensions):
                            print(f"Found video request: {url}")
                            video_urls.add(url)
                
                except (json.JSONDecodeError, KeyError, TypeError) as e:
                    continue
                    
        except Exception as e:
            print(f"Error reading network logs: {e}")
        
        return video_urls
    
    def find_embedded_videos(self, soup, base_url):
        """Find embedded videos from common platforms"""
        video_urls = set()
        
        # YouTube embedded videos
        for iframe in soup.find_all('iframe'):
            src = iframe.get('src', '')
            if 'youtube.com/embed/' in src or 'youtu.be/' in src:
                # Extract video ID and construct direct URL
                video_id_match = re.search(r'(?:embed/|youtu\.be/)([a-zA-Z0-9_-]+)', src)
                if video_id_match:
                    video_id = video_id_match.group(1)
                    # Note: YouTube direct URLs require additional processing with youtube-dl
                    video_urls.add(f"https://www.youtube.com/watch?v={video_id}")
        
        # Vimeo embedded videos
        for iframe in soup.find_all('iframe'):
            src = iframe.get('src', '')
            if 'vimeo.com' in src:
                video_urls.add(src)
        
        return video_urls
    
    def close(self):
        """Close the browser driver"""
        if self.driver:
            self.driver.quit()


def main():
    if len(sys.argv) != 2:
        print("Usage: python video_extractor.py <URL>")
        sys.exit(1)
    
    url = sys.argv[1]
    extractor = VideoExtractor()
    
    try:
        curl_command = extractor.find_main_video(url)
        
        if curl_command:
            print(f"\nValidated curl command to download video:")
            print(curl_command)
        else:
            print("Failed to find a valid video URL.")
            
    except KeyboardInterrupt:
        print("\nOperation cancelled by user.")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        extractor.close()


if __name__ == '__main__':
    main()
