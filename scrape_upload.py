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


def scrape_video_url(page_url, server_preference='EarnVids'):
    """
    Scrape the video URL from the TV10 page using Playwright for JavaScript-rendered content
    """
    try:
        from playwright.sync_api import sync_playwright
        
        with sync_playwright() as p:
            # Launch browser in headless mode
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36')
            
            try:
                # Load the page
                page.goto(page_url, wait_until='domcontentloaded')
                
                # Wait a bit for JavaScript to execute
                import time
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


def decode_server_url(server_url):
    """
    Decode video URL from streaming server (hgcloud.to, vidaraa.cc, etc.)
    """
    try:
        from playwright.sync_api import sync_playwright
        
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36')
            
            try:
                print(f"Decoding server URL: {server_url}")
                page.goto(server_url, wait_until='networkidle')
                
                # Wait for video to load
                import time
                time.sleep(5)
                
                # Look for video element
                video_elem = page.query_selector('video')
                if video_elem:
                    video_src = video_elem.get_attribute('src')
                    if video_src:
                        print(f"Found direct video URL: {video_src}")
                        return video_src
                
                # Look for source elements
                source_elems = page.query_selector_all('source')
                for source in source_elems:
                    src = source.get_attribute('src')
                    if src:
                        print(f"Found source URL: {src}")
                        return src
                
                # Look for iframe
                iframe = page.query_selector('iframe')
                if iframe:
                    iframe_src = iframe.get_attribute('src')
                    if iframe_src:
                        print(f"Found iframe: {iframe_src}")
                        return iframe_src
                
                print("Could not extract direct video URL, returning server URL")
                return server_url
                
            finally:
                browser.close()
                
    except Exception as e:
        print(f"Error decoding server URL: {e}")
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


def upload_to_earnvids(video_url, api_key, title='Video'):
    """
    Upload video to earnvidsapi using File Upload API
    """
    try:
        # Step 1: Get upload server
        print("Getting upload server...")
        server_url = "https://earnvidsapi.com/api/upload/server"
        server_params = {'key': api_key}
        server_response = requests.get(server_url, params=server_params, timeout=30)
        server_response.raise_for_status()
        server_result = server_response.json()
        
        if server_result.get('status') != 200:
            print(f"Failed to get upload server: {server_result.get('msg')}")
            return None
        
        upload_server = server_result.get('result')
        print(f"Upload server: {upload_server}")
        
        # Step 2: Download the video file
        print("Downloading video file...")
        video_response = requests.get(video_url, stream=True, timeout=120)
        video_response.raise_for_status()
        
        # Get file size
        file_size = int(video_response.headers.get('content-length', 0))
        print(f"Video file size: {file_size / (1024*1024):.2f} MB")
        
        # Check if file is too large (earnvidsapi might have limits)
        if file_size > 5 * 1024 * 1024 * 1024:  # 5GB limit
            print("Error: File too large (> 5GB)")
            return None
        
        # Save video to temporary file
        import tempfile
        import os
        with tempfile.NamedTemporaryFile(delete=False, suffix='.mp4') as tmp_file:
            tmp_path = tmp_file.name
            for chunk in video_response.iter_content(chunk_size=8192):
                if chunk:
                    tmp_file.write(chunk)
        
        print(f"Video saved to: {tmp_path}")
        
        # Step 3: Upload the file
        print("Uploading to earnvids...")
        with open(tmp_path, 'rb') as video_file:
            files = {'file': video_file}
            data = {
                'key': api_key,
                'file_title': title
            }
            upload_response = requests.post(upload_server, files=files, data=data, timeout=300)
            upload_response.raise_for_status()
        
        # Clean up temporary file
        os.unlink(tmp_path)
        
        upload_result = upload_response.json()
        
        if upload_result.get('status') == 200:
            files_info = upload_result.get('files', [])
            if files_info:
                return files_info[0]  # Return first file info
        else:
            print(f"Upload failed: {upload_result.get('msg')}")
            return None
            
    except Exception as e:
        print(f"Upload error: {e}")
        # Clean up temporary file if exists
        if 'tmp_path' in locals() and os.path.exists(tmp_path):
            os.unlink(tmp_path)
        return None


def main():
    # Get parameters from environment or command line
    page_url = os.environ.get('PAGE_URL')
    api_key = os.environ.get('EARNVIDS_API_KEY')
    server_preference = os.environ.get('SERVER_PREFERENCE', 'EarnVids')
    
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
        result = upload_to_earnvids(video_url, api_key, title='Video')
        
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
