# vidurl

**Video URL extractor using Selenium WebDriver - Extract downloadable video URLs from web pages and generate validated curl commands.**

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Python 3.13+](https://img.shields.io/badge/python-3.13+-blue.svg)](https://www.python.org/downloads/)

## Features

- **Multiple Detection Strategies**: HTML5 video tags, JavaScript parsing, network request monitoring, and embedded platform detection
- **Modern JavaScript Support**: Handles dynamic content loading with Selenium WebDriver
- **Curl Command Generation**: Outputs complete, validated curl commands with browser-like headers and session cookies
- **Network Request Monitoring**: Captures video requests through Chrome performance logs
- **Interactive Triggering**: Automatically clicks play buttons to trigger video loading
- **Embedded Platform Support**: Detects YouTube and Vimeo iframe embeds

## Installation

### From PyPI (Recommended)

```bash
uv add vidurl
```

Or using pip:
```bash
pip install vidurl
```

### Development Installation

```bash
git clone https://github.com/gfrmin/vidurl.git
cd vidurl
uv sync
```

Or using pip:
```bash
pip install -e .[dev]
```

## Requirements

- **Python 3.13+**
- **Chrome/Chromium browser** installed and available in PATH
- **ChromeDriver** installed and available in PATH
- **curl** command available for URL validation and downloading

### Installing ChromeDriver

**Ubuntu/Debian:**
```bash
sudo apt-get install chromium-chromedriver
```

**macOS:**
```bash
brew install chromedriver
```

**Windows:**
Download from [ChromeDriver downloads](https://chromedriver.chromium.org/downloads) and add to PATH.

## Usage

### Command Line

```bash
# Basic usage
vidurl https://example.com/video-page

# Alternative command
python -m vidurl https://example.com/video-page
```

### Example Output

```bash
$ vidurl https://example.com/video-page

curl -L \
  -H "User-Agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36" \
  -H "Referer: https://example.com/video-page" \
  -H "Accept: video/webm,video/ogg,video/*;q=0.9,application/ogg;q=0.7,audio/*;q=0.6,*/*;q=0.5" \
  -H "Accept-Language: en-US,en;q=0.5" \
  -H "Accept-Encoding: gzip, deflate" \
  -H "DNT: 1" \
  -H "Connection: keep-alive" \
  -H "Sec-Fetch-Dest: video" \
  -H "Sec-Fetch-Mode: no-cors" \
  -H "Sec-Fetch-Site: same-origin" \
  --cookie "session=abc123; preferences=xyz789" \
  --progress-bar \
  -o "video.mp4" \
  "https://example.com/path/to/video.mp4"
```

## Configuration

Create a `config.json` file in your working directory (optional):

```json
{
    "selenium": {
        "headless": true,
        "timeout": 30,
        "implicit_wait": 10
    },
    "detection": {
        "enable_network_monitoring": true,
        "enable_interaction": true,
        "interaction_timeout": 5
    }
}
```

See `config.example.json` for all available options.

## How It Works

vidurl uses multiple strategies to detect video URLs:

1. **HTML5 Video Detection**: Searches for `<video>` and `<source>` tags in the page HTML
2. **JavaScript Parsing**: Uses regex patterns to find video URLs in JavaScript code
3. **Network Request Monitoring**: Monitors Chrome performance logs to capture video requests
4. **Interactive Triggering**: Clicks play buttons and video elements to trigger loading
5. **Embedded Platform Detection**: Detects YouTube and Vimeo iframe embeds

The tool validates all found URLs with HEAD requests and generates curl commands with:
- Browser-like headers (User-Agent, Referer, Accept, etc.)
- Session cookies from the browser session
- Security headers (Sec-Fetch-*)
- Progress bar and appropriate output filename

## Development

### Setup Development Environment

```bash
# Clone the repository
git clone https://github.com/gfrmin/vidurl.git
cd vidurl

# Install using uv (recommended)
uv sync

# Or install with pip
pip install -e .[dev]
```

### Running Tests

```bash
pytest
```

### Code Formatting

```bash
# Format code
black vidurl/ --line-length 100

# Sort imports
isort vidurl/ --profile black

# Lint code
flake8 vidurl/
```

## Troubleshooting

### ChromeDriver Issues

**Error: "chromedriver not found in PATH"**
- Install ChromeDriver and ensure it's in your system PATH
- On Ubuntu: `sudo apt-get install chromium-chromedriver`

**Error: "chrome not reachable"**
- Install Chrome/Chromium browser
- On Ubuntu: `sudo apt-get install chromium-browser`

### Network Issues

**403 Forbidden errors:**
- The tool uses browser-like headers and cookies to minimize detection
- Some sites may still block automated requests

**Timeout errors:**
- Increase timeout values in your config.json
- Check your internet connection

### Video Detection Issues

**No videos found:**
- Try running with interaction enabled (default)
- Some videos only load when play buttons are clicked
- Check if the site requires JavaScript to load video URLs

## Legal Notice

**Important**: This tool is intended for legitimate use cases such as:
- Downloading videos you own or have permission to download
- Academic research and analysis
- Backup of legally accessible content

**Please respect website terms of service and copyright laws.** Users are responsible for ensuring their use complies with applicable laws and website terms of service.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

This project is licensed under the GNU General Public License v3.0 or later - see the [LICENSE](LICENSE) file for details.

## Support

If you encounter any issues or have questions:
- Check the troubleshooting section above
- Search existing [GitHub Issues](https://github.com/gfrmin/vidurl/issues)
- Create a new issue with detailed information about your problem