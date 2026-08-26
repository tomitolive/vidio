#!/usr/bin/env python3
"""
Video Scraper & Upload Script
Scrapes video from TV10 and uploads to earnvidsapi.com
"""

import requests
from bs4 import BeautifulSoup
import json
import sys
import os
from urllib.parse import urljoin, urlparse
import yt_dlp


class ServersList:
    """Class to decode video URLs from iframe sources"""
    
    @staticmethod
    def decode_url(iframe_url):
        """
        Decode the actual video URL from iframe source
        """
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        try:
            # Handle morencius.com URLs
            if 'morencius.com' in iframe_url:
                print("Decoding morencius.com URL...")
                response = requests.get(iframe_url, headers=headers, timeout=30)
                response.raise_for_status()
                
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # Look for iframe with video source
                iframe = soup.find('iframe')
                if iframe and iframe.get('src'):
                    video_src = iframe['src']
                    # Make absolute URL if needed
                    if not video_src.startswith('http'):
                        video_src = urljoin(iframe_url, video_src)
                    print(f"Found video source: {video_src}")
                    return video_src
                
                # Look for video source in script tags
                for script in soup.find_all('script'):
                    if script.string:
                        # Try to find video URLs in the script
                        if '.mp4' in script.string or 'source' in script.string.lower():
                            print("Found potential video source in script")
                            # Return the iframe URL as fallback for now
                            return iframe_url
                
                # Look for video tag with src
                video = soup.find('video')
                if video and video.get('src'):
                    video_src = video['src']
                    if not video_src.startswith('http'):
                        video_src = urljoin(iframe_url, video_src)
                    print(f"Found video tag source: {video_src}")
                    return video_src
                
                # Look for source tags inside video
                for source in soup.find_all('source'):
                    if source.get('src'):
                        video_src = source['src']
                        if not video_src.startswith('http'):
                            video_src = urljoin(iframe_url, video_src)
                        print(f"Found source tag: {video_src}")
                        return video_src
                
                # Fallback: return iframe URL
                print("Could not extract direct video URL, using iframe URL")
                return iframe_url
            
            # Handle vidaraa.cc URLs
            elif 'vidaraa.cc' in iframe_url:
                print("Decoding vidaraa.cc URL...")
                response = requests.get(iframe_url, headers=headers, timeout=30)
                response.raise_for_status()
                
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # Look for iframe with video source
                iframe = soup.find('iframe')
                if iframe and iframe.get('src'):
                    video_src = iframe['src']
                    if not video_src.startswith('http'):
                        video_src = urljoin(iframe_url, video_src)
                    print(f"Found video source: {video_src}")
                    return video_src
                
                # Look for video tag
                video = soup.find('video')
                if video and video.get('src'):
                    video_src = video['src']
                    if not video_src.startswith('http'):
                        video_src = urljoin(iframe_url, video_src)
                    print(f"Found video tag source: {video_src}")
                    return video_src
                
                print("Could not extract direct video URL from vidaraa.cc")
                return iframe_url
            
            # For other services, return as-is
            return iframe_url
            
        except Exception as e:
            print(f"Error decoding URL: {e}")
            return iframe_url


def scrape_category_urls(category_url):
    """
    Scrape all video URLs from a category page using posts-list structure
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    
    try:
        response = requests.get(category_url, headers=headers, timeout=30)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Look for posts-list structure
        posts_list = soup.find('ul', class_='posts-list')
        
        video_urls = []
        
        if posts_list:
            # Extract video URLs from movieItem elements
            for movie_item in posts_list.find_all('li', class_='movieItem'):
                link = movie_item.find('a', href=True)
                if link and link.get('href'):
                    href = link['href']
                    # Make absolute URL
                    if not href.startswith('http'):
                        href = urljoin(category_url, href)
                    
                    # Get title if available
                    title_elem = link.find('h1', class_='BottomTitle')
                    title = title_elem.text if title_elem else 'Unknown'
                    
                    video_urls.append({
                        'url': href,
                        'title': title
                    })
        else:
            # Fallback: try to find links with common patterns
            for link in soup.find_all('a', href=True):
                href = link['href']
                # Make absolute URL
                if not href.startswith('http'):
                    href = urljoin(category_url, href)
                
                # Filter for video pages (adjust pattern as needed)
                if '/مشاهدة-' in href or 'watch' in href or 'video' in href:
                    if href not in [v['url'] for v in video_urls]:
                        video_urls.append({'url': href, 'title': 'Unknown'})
        
        print(f"Found {len(video_urls)} video URLs in category")
        return video_urls
            
    except Exception as e:
        print(f"Error scraping category page: {e}")
        return []


def scrape_video_title(page_url):
    """
    Scrape the video title from the page using Playwright
    Returns title like 'Predator Badlands 2025' from the page's <title> tag
    """
    try:
        from playwright.sync_api import sync_playwright
        try:
            from playwright_stealth import stealth_sync
        except ImportError:
            stealth_sync = None
        
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"]
            )
            page = browser.new_page(user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
            if stealth_sync:
                stealth_sync(page)
            
            try:
                page.goto(page_url, wait_until='domcontentloaded', timeout=30000)
                import time
                if "Just a moment" in page.title() or "Cloudflare" in page.title():
                    print("Cloudflare challenge detected, waiting...")
                    try:
                        page.wait_for_function('!document.title.includes("Just a moment") && !document.title.includes("Cloudflare")', timeout=20000)
                    except Exception:
                        pass
                time.sleep(2)
                
                # Get page title (e.g. "مشاهدة فيلم Predator Badlands 2025 مترجم | ايجي ديد")
                page_title = page.title()
                
                # Extract movie name: remove Arabic prefix/suffix and domain
                import re
                # Remove Arabic text and clean up
                title = page_title
                # Remove everything after | or - (domain name)
                title = re.split(r'\s*[|\-]\s*', title)[0]
                # Remove Arabic words (Unicode range)
                title = re.sub(r'[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]+', ' ', title)
                # Remove common Arabic words like "مشاهدة فيلم"
                title = re.sub(r'[\s]+', ' ', title).strip()
                
                if title:
                    print(f"Scraped title: {title}")
                    return title
                
                return None
            finally:
                browser.close()
    except Exception as e:
        print(f"Error scraping title: {e}")
        return None


def scrape_video_url(page_url, server_preference='EarnVids'):
    """
    Scrape the video URL from the TV10 page using Playwright for JavaScript-rendered content
    """
    try:
        from playwright.sync_api import sync_playwright
        try:
            from playwright_stealth import stealth_sync
        except ImportError:
            stealth_sync = None
        
        with sync_playwright() as p:
            # Launch browser in headless mode
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"]
            )
            page = browser.new_page(user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36')
            if stealth_sync:
                stealth_sync(page)
            
            try:
                # Load the page
                page.goto(page_url, wait_until='domcontentloaded')
                
                # Wait a bit for JavaScript to execute
                import time
                if "Just a moment" in page.title() or "Cloudflare" in page.title():
                    print("Cloudflare challenge detected, waiting...")
                    try:
                        page.wait_for_function('!document.title.includes("Just a moment") && !document.title.includes("Cloudflare")', timeout=20000)
                    except Exception:
                        pass
                time.sleep(2)
                
                # Check if this is a details page (has fa-play button)
                play_button = page.query_selector('.fi-play, .fa-play, i[class*="play"]')
                
                if play_button:
                    print("Found play button - this is a details page")
                    # Click the play button to navigate to watch page
                    play_button.click()
                    # Wait for navigation
                    time.sleep(3)
                    # Get the new URL (watch page)
                    watch_url = page.url
                    print(f"Navigated to watch page: {watch_url}")
                else:
                    print("No play button found - this might already be a watch page")
                    watch_url = page_url
                
                # Try to find any element with data-link attribute
                data_links = page.query_selector_all('[data-link]')
                
                if data_links:
                    print(f"Found {len(data_links)} elements with data-link attribute")
                    servers = []
                    for elem in data_links:
                        link = elem.get_attribute('data-link')
                        # Try to get the name from the element
                        name_elem = elem.query_selector('p')
                        name = name_elem.text_content() if name_elem else 'Unknown'
                        servers.append({'name': name, 'link': link})
                    
                    print(f"Found {len(servers)} servers:")
                    for server in servers:
                        print(f"  - {server['name']}: {server['link']}")
                    
                    # Try to find preferred server (case-insensitive)
                    for server in servers:
                        if server_preference.lower() in server['name'].lower():
                            print(f"Selected server: {server['name']}")
                            return server['link']
                    
                    # If preferred not found, use first one
                    if servers:
                        print(f"Preferred server '{server_preference}' not found, using first available: {servers[0]['name']}")
                        return servers[0]['link']
                else:
                    print("No elements with data-link found")
                
                # Get the page HTML after JavaScript execution
                page_html = page.content()
                soup = BeautifulSoup(page_html, 'html.parser')
                
                # Find serversList
                servers_list = soup.find('ul', class_='serversList')
                
                if servers_list:
                    # Extract all server links
                    servers = []
                    for li in servers_list.find_all('li', {'data-link': True}):
                        server_name = li.find('p').text if li.find('p') else 'Unknown'
                        server_link = li['data-link']
                        servers.append({'name': server_name, 'link': server_link})
                    
                    print(f"Found {len(servers)} servers in HTML:")
                    for server in servers:
                        print(f"  - {server['name']}: {server['link']}")
                    
                    # Try to find preferred server (case-insensitive)
                    for server in servers:
                        if server_preference.lower() in server['name'].lower():
                            print(f"Selected server: {server['name']}")
                            return server['link']
                    
                    # If preferred not found, use first one
                    if servers:
                        print(f"Preferred server '{server_preference}' not found, using first available: {servers[0]['name']}")
                        return servers[0]['link']
                else:
                    print("No serversList found even after JavaScript execution")
                    
                    # Fallback to iframe
                    iframe = soup.find('iframe')
                    if iframe and iframe.get('src'):
                        iframe_src = iframe['src']
                        print(f"Found iframe: {iframe_src}")
                        return iframe_src
                    
                    return None
                    
            finally:
                browser.close()
                
    except ImportError:
        print("Playwright not installed, falling back to requests...")
        return scrape_video_url_fallback(page_url, server_preference)
    except Exception as e:
        print(f"Error with Playwright: {e}")
        return scrape_video_url_fallback(page_url, server_preference)
    except Exception as e:
        print(f"Error with Playwright: {e}")
        return scrape_video_url_fallback(page_url, server_preference)


def decode_vidaraa_url(server_url):
    """
    Decode video URL from vidaraa.cc using their /api/stream endpoint
    """
    try:
        from urllib.parse import urlparse
        
        # Extract filecode from URL
        path = urlparse(server_url).path
        filecode = path.strip('/').split('/')[-1]
        
        if not filecode:
            print("Could not extract filecode from vidaraa.cc URL")
            return server_url
        
        print(f"Decoding vidaraa.cc URL, filecode: {filecode}")
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Content-Type': 'application/json',
            'Origin': 'https://vidaraa.cc',
            'Referer': server_url,
        }
        
        api_url = 'https://vidaraa.cc/api/stream'
        resp = requests.post(api_url, 
            json={"filecode": filecode, "device": "web"},
            headers=headers,
            timeout=15)
        
        if resp.status_code == 200:
            data = resp.json()
            streaming_url = data.get('streaming_url')
            if streaming_url:
                print(f"Found streaming URL: {streaming_url[:100]}...")
                return streaming_url
        
        print(f"vidaraa.cc API returned status {resp.status_code}")
        return server_url
        
    except Exception as e:
        print(f"Error decoding vidaraa.cc URL: {e}")
        return server_url


def decode_server_url(server_url):
    """
    Decode video URL from streaming server (voe.sx, hgcloud.to, vidaraa.cc, etc.)
    Uses yt-dlp to extract direct video URLs, falls back to Playwright
    """
    # Handle voe.sx directly with Playwright (yt-dlp doesn't support it)
    if 'voe.sx' in server_url:
        print(f"voe.sx detected, using Playwright decoder...")
        return decode_server_url_playwright(server_url)

    # Handle vidaraa.cc via API (returns direct streaming URL)
    if 'vidaraa.cc' in server_url:
        return decode_vidaraa_url(server_url)

    try:
        import yt_dlp
        
        print(f"Decoding server URL with yt-dlp: {server_url}")
        
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
            'format': 'best',
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(server_url, download=False)
            
            if info:
                # Get the best format URL
                if 'formats' in info and info['formats']:
                    # Get the best format
                    best_format = info['formats'][0]
                    video_url = best_format.get('url')
                    if video_url:
                        print(f"Found direct video URL: {video_url}")
                        return video_url
                
                # Fallback to url field
                if 'url' in info:
                    print(f"Found video URL: {info['url']}")
                    return info['url']
        
        print("Could not extract video URL with yt-dlp, returning server URL")
        return server_url
        
    except ImportError:
        print("yt-dlp not installed, falling back to Playwright...")
        return decode_server_url_playwright(server_url)
    except Exception as e:
        print(f"Error decoding server URL with yt-dlp: {e}")
        return decode_server_url_playwright(server_url)


def decode_server_url_playwright(server_url):
    """
    Fallback method using Playwright to extract video URL
    """
    try:
        from playwright.sync_api import sync_playwright
        try:
            from playwright_stealth import stealth_sync
        except ImportError:
            stealth_sync = None
        
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"]
            )
            
            context = browser.new_context(user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
            page = context.new_page()
            if stealth_sync:
                stealth_sync(page)
            
            video_url_found = None
            
            def handle_response(response):
                nonlocal video_url_found
                url = response.url
                ct = response.headers.get('content-type', '')
                
                # Exclude non-video file types
                exclude_exts = ['.woff', '.woff2', '.ttf', '.otf', '.js', '.css', '.png', '.jpg', '.gif', '.svg', '.ico', '.json', '.xml', '.html']
                url_lower = url.lower()
                if any(url_lower.endswith(ext) or (ext + '?') in url_lower for ext in exclude_exts):
                    return
                
                # Check if this is a video file response
                is_video_ext = any(url_lower.endswith(ext) or url_lower.endswith(ext + '/') or (ext + '?') in url_lower or f'.{ext}?' in url_lower for ext in ['.mp4', '.m3u8', '.ts', '.webm', '.mkv'])
                is_video_ct = 'video' in ct
                
                if is_video_ext or is_video_ct:
                    if 'error' not in url_lower and 'ping' not in url_lower and 'jwpltx' not in url_lower:
                        if not video_url_found:
                            video_url_found = url
                            print(f"Intercepted video URL: {url}")
            
            page.on('response', handle_response)
            
            try:
                print(f"Decoding server URL with Playwright: {server_url}")
                page.goto(server_url, wait_until='domcontentloaded', timeout=30000)
                
                # Wait for page scripts to load
                import time
                time.sleep(5)
                
                # Try clicking play button to trigger video loading
                try:
                    play_btn = page.query_selector('button[class*="play"], .play-btn, #play-btn, .vjs-big-play-button, button[aria-label*="Play"], .plyr__control, .jw-display-icon-play')
                    if play_btn and play_btn.is_visible():
                        print("Found play button, clicking...")
                        play_btn.click()
                        time.sleep(10)
                except Exception:
                    pass
                
                # Wait for network interception
                time.sleep(10)
                
                if video_url_found:
                    print(f"Found video URL via network interception: {video_url_found}")
                    return video_url_found
                
                # Fallback: Try to get page content and look for video URLs in scripts
                page_content = page.content()
                
                # Look for common video URL patterns in scripts
                import re
                video_patterns = [
                    r'(https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*)',
                    r'(https?://[^\s"\'<>]+\.mp4[^\s"\'<>]*)',
                    r'(https?://[^\s"\'<>]+/video/[^\s"\'<>]*)',
                    r'"url"\s*:\s*"(https?://[^"]+)"',
                    r'"file"\s*:\s*"(https?://[^"]+)"',
                    r'"source"\s*:\s*"(https?://[^"]+)"',
                    r"var\s+source\s*=\s*['\"]([^'\"]+)['\"]",
                ]
                
                # Filter out known test/decoy URLs and non-video files
                decoy_patterns = ['test-videos.co', 'bigbuckbunny', 'jwpltx.com', 'jwpcdn.com']
                non_video_exts = ['.woff', '.woff2', '.ttf', '.otf', '.js', '.css', '.png', '.jpg', '.gif', '.svg', '.ico']
                
                for pattern in video_patterns:
                    matches = re.findall(pattern, page_content)
                    if matches:
                        for match in matches:
                            if match.startswith('http') and 'error' not in match.lower() and 'ping' not in match.lower():
                                match_lower = match.lower()
                                # Skip decoy/test URLs
                                if any(dp in match_lower for dp in decoy_patterns):
                                    print(f"Skipping decoy URL: {match}")
                                    continue
                                # Skip non-video files
                                if any(match_lower.endswith(ext) or (ext + '?') in match_lower for ext in non_video_exts):
                                    print(f"Skipping non-video file: {match}")
                                    continue
                                print(f"Found video URL in page content: {match}")
                                return match
                
                # Look for video element
                video_elem = page.query_selector('video')
                if video_elem:
                    video_src = video_elem.get_attribute('src')
                    if video_src and video_src.startswith('http'):
                        print(f"Found direct video URL: {video_src}")
                        return video_src
                    
                    # Check for source children
                    source_elems = page.query_selector_all('video source')
                    for source in source_elems:
                        src = source.get_attribute('src')
                        if src and src.startswith('http'):
                            print(f"Found source URL: {src}")
                            return src
                
                # Look for source elements
                source_elems = page.query_selector_all('source')
                for source in source_elems:
                    src = source.get_attribute('src')
                    if src and src.startswith('http'):
                        print(f"Found source URL: {src}")
                        return src
                
                # Look for iframe
                iframe = page.query_selector('iframe')
                if iframe:
                    iframe_src = iframe.get_attribute('src')
                    if iframe_src and iframe_src.startswith('http') and not iframe_src.startswith('javascript'):
                        print(f"Found iframe: {iframe_src}")
                        return iframe_src
                
                print("Could not extract direct video URL, returning server URL")
                return server_url
                
            finally:
                context.close()
                browser.close()
                
    except Exception as e:
        print(f"Error decoding server URL with Playwright: {e}")
        return server_url


def scrape_video_url_fallback(page_url, server_preference='EarnVids'):
    """
    Fallback method using requests only
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    
    try:
        response = requests.get(page_url, headers=headers, timeout=30)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Extract post ID from the page
        body = soup.find('body')
        post_id = None
        if body and body.get('class'):
            for cls in body.get('class'):
                if cls.startswith('postid-'):
                    post_id = cls.replace('postid-', '')
                    break
        
        print(f"Post ID: {post_id}")
        
        # Try to load servers via Ajax
        try:
            ajax_url = "https://tv10.egydead.live/wp-admin/admin-ajax.php"
            ajax_data = {
                'action': 'get_servers',
                'post_id': post_id
            }
            ajax_response = requests.post(ajax_url, data=ajax_data, headers=headers, timeout=30)
            if ajax_response.status_code == 200 and ajax_response.text:
                print("Ajax response received")
                print(f"Response length: {len(ajax_response.text)}")
                ajax_soup = BeautifulSoup(ajax_response.text, 'html.parser')
                servers_list = ajax_soup.find('ul', class_='serversList')
                if servers_list:
                    print("Found serversList in Ajax response")
        except Exception as e:
            print(f"Ajax request failed: {e}")
        
        # Fallback: try to find iframe with video
        iframe = soup.find('iframe')
        
        if not iframe:
            iframe = soup.find('iframe', {'src': True})
        
        if iframe and iframe.get('src'):
            iframe_src = iframe['src']
            if not iframe_src.startswith('http'):
                iframe_src = urljoin(page_url, iframe_src)
            
            print(f"Found iframe: {iframe_src}")
            return iframe_src
        else:
            print("No iframe found on the page")
            return None
            
    except Exception as e:
        print(f"Error scraping page: {e}")
        return None


def rename_file(api_key, filecode, title):
    """Rename uploaded file on DoodStream"""
    try:
        url = f'https://doodapi.com/api/file/rename?key={api_key}&file_code={filecode}&title={title}'
        resp = requests.get(url, timeout=15)
        data = resp.json()
        if data.get('status') == 200:
            print(f"Renamed to: {title}")
        else:
            print(f"Rename failed: {data.get('msg')}")
    except Exception as e:
        print(f"Rename error: {e}")


def upload_to_earnvids(video_url, api_key, title='Video'):
    """
    Upload video to DoodStream.
    For direct URLs: uses remote upload API.
    For m3u8/HLS: downloads locally first, then uploads file.
    """
    import tempfile
    import subprocess
    
    # Check if this is an m3u8/HLS URL (token-based, expires quickly)
    if '.m3u8' in video_url:
        print("HLS/m3u8 URL detected - downloading locally first...")
        
        # Download using yt-dlp
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = os.path.join(tmpdir, 'video.mp4')
            
            try:
                ydl_opts = {
                    'quiet': True,
                    'no_warnings': True,
                    'format': 'best[ext=mp4]/best',
                    'outtmpl': output_path,
                    'merge_output_format': 'mp4',
                }
                
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([video_url])
                
                # Find the downloaded file (yt-dlp might add extension)
                downloaded_files = [f for f in os.listdir(tmpdir) if f.endswith('.mp4')]
                if not downloaded_files:
                    downloaded_files = os.listdir(tmpdir)
                
                if not downloaded_files:
                    print("Download failed - no file found")
                    return None
                
                filepath = os.path.join(tmpdir, downloaded_files[0])
                file_size = os.path.getsize(filepath)
                print(f"Downloaded: {downloaded_files[0]} ({file_size / (1024*1024):.1f} MB)")
                
                # Get upload server
                server_resp = requests.get(
                    f'https://doodapi.com/api/upload/server?key={api_key}',
                    timeout=30)
                server_data = server_resp.json()
                
                if server_data.get('status') != 200:
                    print(f"Failed to get upload server: {server_data.get('msg')}")
                    return None
                
                upload_server = server_data['result']
                print(f"Upload server: {upload_server}")
                
                # Upload file
                with open(filepath, 'rb') as f:
                    files = {'file': (downloaded_files[0], f, 'video/mp4')}
                    data = {'api_key': api_key}
                    
                    upload_resp = requests.post(
                        upload_server,
                        data=data,
                        files=files,
                        timeout=600)
                    upload_result = upload_resp.json()
                
                if upload_result.get('status') == 200:
                    result = upload_result.get('result', [{}])[0]
                    filecode = result.get('filecode')
                    print(f"Upload successful! File code: {filecode}")
                    # Rename file with proper title
                    if title and filecode:
                        rename_file(api_key, filecode, title)
                    return result
                else:
                    print(f"Upload failed: {upload_result.get('msg')}")
                    return None
                    
            except Exception as e:
                print(f"Error during download/upload: {e}")
                return None
    
    # For direct URLs (mp4, etc.) - use remote upload
    try:
        print("Uploading to DoodStream via remote URL...")
        
        upload_url = f'https://doodapi.com/api/upload/url?key={api_key}&url={video_url}'
        
        upload_response = requests.get(upload_url, timeout=60)
        upload_result = upload_response.json()
        
        if upload_result.get('status') == 200:
            result = upload_result.get('result', {})
            filecode = result.get('filecode')
            print(f"Upload successful! File code: {filecode}")
            # Rename file with proper title
            if title and filecode:
                rename_file(api_key, filecode, title)
            return result
        else:
            print(f"Upload failed: {upload_result.get('msg')}")
            return None
        
    except Exception as e:
        print(f"Error uploading to DoodStream: {e}")
        return None


def main():
    # Get parameters from environment or command line
    page_url = os.environ.get('PAGE_URL')
    api_key = os.environ.get('EARNVIDS_API_KEY')
    server_preference = os.environ.get('SERVER_PREFERENCE', 'Streamix')
    
    if not page_url:
        print("Error: PAGE_URL environment variable not set")
        sys.exit(1)
    
    if not api_key:
        print("Error: EARNVIDS_API_KEY environment variable not set")
        sys.exit(1)
    
    print(f"Scraping URL: {page_url}")
    
    # Check if it's a category page
    if '/category/' in page_url:
        print("Detected category page - scraping all videos...")
        video_data = scrape_category_urls(page_url)
        
        if not video_data:
            print("No video URLs found in category")
            sys.exit(1)
        
        print(f"Processing {len(video_data)} videos...")
        
        results = []
        for idx, video_info in enumerate(video_data, 1):
            video_url = video_info['url']
            video_title = video_info.get('title', 'Unknown')
            print(f"\n[{idx}/{len(video_data)}] Processing: {video_title}")
            print(f"URL: {video_url}")
            
            # Scrape the iframe URL
            iframe_url = scrape_video_url(video_url, server_preference)
            
            if not iframe_url:
                print(f"Failed to scrape video URL for {video_url}")
                results.append({
                    'success': False,
                    'title': video_title,
                    'original_url': video_url,
                    'error': 'Failed to scrape video URL'
                })
                continue
            
            print(f"Found iframe URL: {iframe_url}")
            
            # Decode the actual video URL using server decoder
            decoded_url = decode_server_url(iframe_url)
            
            print(f"Video URL: {decoded_url}")
            
            # Upload to earnvids
            print("Uploading to earnvids...")
            result = upload_to_earnvids(decoded_url, api_key, title=video_title)
            
            if result:
                filecode = result.get('filecode')
                print(f"Upload successful! File code: {filecode}")
                results.append({
                    'success': True,
                    'filecode': filecode,
                    'title': video_title,
                    'original_url': video_url,
                    'iframe_url': iframe_url,
                    'video_url': decoded_url
                })
            else:
                print("Upload failed")
                results.append({
                    'success': False,
                    'title': video_title,
                    'original_url': video_url,
                    'iframe_url': iframe_url,
                    'video_url': decoded_url,
                    'error': 'Upload failed'
                })
        
        # Save all results to JSON file
        output = {
            'category_url': page_url,
            'total_videos': len(video_data),
            'successful_uploads': sum(1 for r in results if r['success']),
            'failed_uploads': sum(1 for r in results if not r['success']),
            'results': results
        }
        
        with open('upload_result.json', 'w') as f:
            json.dump(output, f, indent=2)
        
        print(f"\n{'='*50}")
        print(f"Batch processing complete!")
        print(f"Total: {len(video_data)} | Success: {output['successful_uploads']} | Failed: {output['failed_uploads']}")
        print(f"Result saved to upload_result.json")
        
    else:
        # Single video page
        # Scrape title from page
        video_title = scrape_video_title(page_url)
        if not video_title:
            # Fallback: extract from URL
            from urllib.parse import urlparse
            url_path = urlparse(page_url).path.strip('/')
            video_title = url_path.replace('-', '.').replace('/', '')
        
        # Step 1: Scrape the iframe URL
        iframe_url = scrape_video_url(page_url, server_preference)
        
        if not iframe_url:
            print("Failed to scrape video URL")
            sys.exit(1)
        
        print(f"Found iframe URL: {iframe_url}")
        
        # Step 2: Decode the actual video URL using server decoder
        video_url = decode_server_url(iframe_url)
        
        print(f"Video URL: {video_url}")
        
        # Step 3: Upload to earnvids
        print("Uploading to earnvids...")
        result = upload_to_earnvids(video_url, api_key, title=video_title)
        
        if result:
            filecode = result.get('filecode')
            print(f"Upload successful! File code: {filecode}")
            
            # Save result to JSON file
            output = {
                'success': True,
                'filecode': filecode,
                'original_url': page_url,
                'iframe_url': iframe_url,
                'video_url': video_url
            }
            
            with open('upload_result.json', 'w') as f:
                json.dump(output, f, indent=2)
            
            print("Result saved to upload_result.json")
        else:
            print("Upload failed")
            sys.exit(1)


if __name__ == "__main__":
    main()
