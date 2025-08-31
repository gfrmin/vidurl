#!/usr/bin/env python3
"""
Video URL Extractor

Extracts direct video URLs from web pages using headless browser automation.
Supports common video hosting platforms and embedded videos.
"""

import argparse
import sys
import time
import re
from urllib.parse import urljoin, urlparse
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException
from bs4 import BeautifulSoup


class VideoExtractor:
    def __init__(self, headless=True, timeout=10):
        self.timeout = timeout
        self.driver = None
        self.setup_driver(headless)
    
    def setup_driver(self, headless):
        """Setup Chrome WebDriver with appropriate options"""
        chrome_options = Options()
        if headless:
            chrome_options.add_argument('--headless')
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--disable-gpu')
        chrome_options.add_argument('--window-size=1920,1080')
        chrome_options.add_argument('--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36')
        
        try:
            self.driver = webdriver.Chrome(options=chrome_options)
        except WebDriverException as e:
            print(f"Error setting up Chrome driver: {e}")
            print("Make sure ChromeDriver is installed and in PATH")
            sys.exit(1)
    
    def extract_video_urls(self, url):
        """Extract video URLs from the given webpage"""
        try:
            print(f"Loading page: {url}")
            self.driver.get(url)
            
            # Wait for page to load
            time.sleep(3)
            
            # Try to find and click play buttons to trigger video loading
            self.trigger_video_loading()
            
            # Get page source after JavaScript execution
            soup = BeautifulSoup(self.driver.page_source, 'html.parser')
            
            video_urls = set()
            
            # Method 1: Find HTML5 video elements
            video_urls.update(self.find_html5_videos(soup, url))
            
            # Method 2: Find video URLs in script tags
            video_urls.update(self.find_videos_in_scripts(soup, url))
            
            # Method 3: Check network requests for video files
            video_urls.update(self.find_videos_in_network())
            
            # Method 4: Look for common video hosting patterns
            video_urls.update(self.find_embedded_videos(soup, url))
            
            return list(video_urls)
            
        except Exception as e:
            print(f"Error extracting videos: {e}")
            return []
    
    def trigger_video_loading(self):
        """Try to trigger video loading by clicking play buttons"""
        play_selectors = [
            'button[aria-label*="play" i]',
            'button[title*="play" i]',
            '.play-button',
            '.video-play-button',
            '[class*="play"]',
            'button:contains("Play")',
            '.vjs-big-play-button'
        ]
        
        for selector in play_selectors:
            try:
                elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                for element in elements:
                    if element.is_displayed():
                        element.click()
                        time.sleep(2)
                        break
            except:
                continue
    
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
            # Get network logs (requires Chrome with logging enabled)
            logs = self.driver.get_log('performance')
            for log in logs:
                message = log.get('message', {})
                if isinstance(message, str):
                    import json
                    try:
                        message = json.loads(message)
                    except:
                        continue
                
                if message.get('message', {}).get('method') == 'Network.responseReceived':
                    response = message['message']['params']['response']
                    url = response.get('url', '')
                    mime_type = response.get('mimeType', '')
                    
                    if any(ext in url.lower() for ext in ['.mp4', '.webm', '.ogg', '.avi', '.mov']):
                        video_urls.add(url)
                    elif 'video' in mime_type.lower():
                        video_urls.add(url)
        except:
            pass  # Network logging might not be available
        
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
    parser = argparse.ArgumentParser(description='Extract direct video URLs from web pages')
    parser.add_argument('url', help='URL of the webpage containing the video')
    parser.add_argument('--no-headless', action='store_true', help='Run browser in non-headless mode')
    parser.add_argument('--timeout', type=int, default=10, help='Timeout in seconds (default: 10)')
    
    args = parser.parse_args()
    
    extractor = VideoExtractor(headless=not args.no_headless, timeout=args.timeout)
    
    try:
        video_urls = extractor.extract_video_urls(args.url)
        
        if video_urls:
            print(f"\nFound {len(video_urls)} video URL(s):")
            for i, url in enumerate(video_urls, 1):
                print(f"{i}. {url}")
        else:
            print("No video URLs found.")
            
    except KeyboardInterrupt:
        print("\nOperation cancelled by user.")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        extractor.close()


if __name__ == '__main__':
    main()
