"""
Command-line interface for the video extractor.
"""

import sys
import argparse
import logging
from typing import Optional

from .config import VideoExtractorConfig
from .extractor import VideoExtractor
from .utils import setup_logging, load_config_from_file
from .exceptions import (
    VideoExtractorError,
    WebDriverSetupError,
    VideoNotFoundError,
    VideoValidationError
)


def parse_arguments() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Extract video URLs from web pages and generate curl download commands",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s https://example.com/video-page
  %(prog)s https://example.com/video-page --output-dir ~/Downloads
  %(prog)s https://example.com/video-page --verbose --timeout 30
  %(prog)s https://example.com/video-page --list-all --dry-run
        """
    )
    
    parser.add_argument('url', help='URL of the web page containing video')
    
    # Output options
    parser.add_argument('--output-dir', '-o', default='.',
                       help='Output directory for downloaded video (default: current directory)')
    parser.add_argument('--filename', '-f',
                       help='Output filename (default: extracted from URL)')
    
    # Timeout options
    parser.add_argument('--timeout', type=int, default=10,
                       help='Timeout for page load and video detection (default: 10)')
    parser.add_argument('--curl-timeout', type=int, default=10,
                       help='Timeout for curl validation requests (default: 10)')
    
    # Browser options
    parser.add_argument('--no-headless', action='store_true',
                       help='Run browser in non-headless mode (visible browser window)')
    parser.add_argument('--user-agent',
                       help='Custom user agent string')
    parser.add_argument('--window-size', default='1920,1080',
                       help='Browser window size (default: 1920,1080)')
    
    # Detection options
    parser.add_argument('--max-retries', type=int, default=3,
                       help='Maximum retry attempts for failed operations (default: 3)')
    parser.add_argument('--parallel', type=int, default=4,
                       help='Number of parallel validation threads (default: 4)')
    
    # Output control
    parser.add_argument('--verbose', '-v', action='store_true',
                       help='Enable verbose output')
    parser.add_argument('--quiet', '-q', action='store_true',
                       help='Suppress non-essential output')
    parser.add_argument('--list-all', action='store_true',
                       help='List all found video URLs instead of selecting the first valid one')
    parser.add_argument('--dry-run', action='store_true',
                       help='Show what would be done without validation or downloading')
    
    # Configuration
    parser.add_argument('--config', type=str,
                       help='Path to JSON configuration file')
    
    args = parser.parse_args()
    
    # Validate arguments
    if args.quiet and args.verbose:
        parser.error("--quiet and --verbose are mutually exclusive")
    
    return args


def create_config_from_args(args: argparse.Namespace) -> VideoExtractorConfig:
    """Create configuration object from command line arguments."""
    config_dict = {}
    
    # Load from config file if provided
    if args.config:
        config_dict.update(load_config_from_file(args.config))
    
    # Override with command line arguments
    config_dict.update({
        'page_load_timeout': args.timeout,
        'curl_timeout': args.curl_timeout,
        'video_detection_timeout': args.timeout * 3,
        'headless': not args.no_headless,
        'window_size': args.window_size,
        'output_dir': args.output_dir,
        'verbose': args.verbose,
        'quiet': args.quiet,
        'max_retries': args.max_retries,
        'max_workers': args.parallel,
    })
    
    if args.user_agent:
        config_dict['user_agent'] = args.user_agent
    
    if args.filename:
        config_dict['default_filename'] = args.filename
    
    return VideoExtractorConfig(**config_dict)


def main() -> None:
    """Main entry point for the video extractor."""
    try:
        # Parse command line arguments
        args = parse_arguments()
        
        # Create configuration from arguments
        config = create_config_from_args(args)
        
        # Setup logging
        setup_logging(config)
        logger = logging.getLogger(__name__)
        
        logger.info(f"Starting video extraction for: {args.url}")
        
        # Use context manager for proper resource cleanup
        with VideoExtractor(config) as extractor:
            # Handle dry-run mode
            if args.dry_run:
                logger.info("DRY RUN MODE: Would extract video from URL without validation")
                print(f"Would extract video from: {args.url}")
                print(f"Output directory: {config.output_dir}")
                print(f"Configuration: headless={config.headless}, timeout={config.page_load_timeout}s")
                return
            
            # Extract video URL and get curl command
            curl_command = extractor.find_main_video(args.url)
            
            if curl_command:
                if not config.quiet:
                    print(f"\nValidated curl command to download video:")
                print(curl_command)
                
                if args.list_all:
                    # This would require modifying find_main_video to return all URLs
                    # For now, just show a message
                    logger.info("Note: --list-all option would show all found URLs (not implemented in this version)")
                
            else:
                if not config.quiet:
                    print("Failed to find a valid video URL.")
                logger.error("No valid video URL found")
                sys.exit(1)
                
    except KeyboardInterrupt:
        print("\nOperation cancelled by user.")
        sys.exit(130)
    except (VideoExtractorError, WebDriverSetupError, VideoNotFoundError, VideoValidationError) as e:
        logger.error(f"Video extraction failed: {e}")
        if not config.quiet if 'config' in locals() else True:
            print(f"Error: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        print(f"Unexpected error: {e}")
        sys.exit(1)