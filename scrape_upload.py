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
import time
import yt_dlp

import random

PROXIES_LIST = [
    "http://ohzgotst:ea339u0rwqy8@31.59.20.176:6754",
    "http://ohzgotst:ea339u0rwqy8@45.38.107.97:6014",
    "http://ohzgotst:ea339u0rwqy8@198.105.121.200:6462",
    "http://ohzgotst:ea339u0rwqy8@64.137.96.74:6641",
    "http://ohzgotst:ea339u0rwqy8@198.23.243.226:6361",
    "http://ohzgotst:ea339u0rwqy8@38.154.185.97:6370",
    "http://ohzgotst:ea339u0rwqy8@84.247.60.125:6095",
    "http://ohzgotst:ea339u0rwqy8@142.111.67.146:5611",
    "http://ohzgotst:ea339u0rwqy8@191.96.254.138:6185",
    "http://ohzgotst:ea339u0rwqy8@31.58.9.4:6077"
]

PROXY_URL = os.environ.get('PROXY_URL', '').strip() or random.choice(PROXIES_LIST)
REQUESTS_PROXIES = {'http': PROXY_URL, 'https': PROXY_URL} if PROXY_URL else None

if PROXY_URL:
    masked_proxy = PROXY_URL.split('@')[-1] if '@' in PROXY_URL else PROXY_URL
    print(f"Using Proxy: {masked_proxy}")


def parse_playwright_proxy(proxy_url):
    if not proxy_url:
        return None
    try:
        parsed = urlparse(proxy_url)
        if parsed.username and parsed.password:
            return {
                'server': f"{parsed.scheme}://{parsed.hostname}:{parsed.port}",
                'username': parsed.username,
                'password': parsed.password
            }
        return {'server': proxy_url}
    except Exception:
        return {'server': proxy_url}


def handle_cloudflare_challenge(page, timeout=60):
    import time
    import random
    start = time.time()
    if "Just a moment" in page.title() or "Cloudflare" in page.title():
        print("Cloudflare challenge detected, waiting and attempting auto-click...")
    else:
        return True

    while time.time() - start < timeout:
        title = page.title()
        if "Just a moment" not in title and "Cloudflare" not in title and title.strip():
            print(f"Cloudflare passed! Page title: {title}")
            return True
        
        # Simulate human-like mouse movements
        try:
            viewport = page.viewport_size
            if viewport:
                x = random.randint(100, viewport['width'] - 100)
                y = random.randint(100, viewport['height'] - 100)
                page.mouse.move(x, y)
                time.sleep(random.uniform(0.1, 0.3))
        except Exception:
            pass
        
        # Try finding Turnstile iframe & clicking
        try:
            iframe = page.query_selector('iframe[src*="challenges.cloudflare.com"]')
            if iframe:
                box = iframe.bounding_box()
                if box:
                    print(f"Found Cloudflare Turnstile iframe at x={box['x']}, y={box['y']}. Clicking...")
                    page.mouse.click(box['x'] + 35, box['y'] + 35)
                    time.sleep(random.uniform(1.5, 2.5))
        except Exception:
            pass

        try:
            for frame in page.frames:
                if 'challenges.cloudflare.com' in frame.url or 'turnstile' in frame.url:
                    cb = frame.query_selector('input[type="checkbox"], .mark, #challenge-stage, label, div[class*="checkbox"], [role="checkbox"], .recaptcha-checkbox-border')
                    if cb:
                        print("Clicking Turnstile checkbox inside frame...")
                        cb.click()
                        time.sleep(random.uniform(1.5, 2.5))
        except Exception:
            pass

        time.sleep(random.uniform(1.0, 2.0))
    return False


def launch_stealth_browser(p, user_agent=None):
    headless_env = os.environ.get('HEADLESS', 'true').lower() == 'true'
    launch_kwargs = {
        'headless': headless_env,
        'args': [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-infobars",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-timer-throttling",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            "--disable-features=TranslateUI",
            "--disable-ipc-flooding-protection",
            "--window-size=1920,1080"
        ]
    }
    pw_proxy = parse_playwright_proxy(PROXY_URL)
    if pw_proxy:
        launch_kwargs['proxy'] = pw_proxy
    browser = p.chromium.launch(**launch_kwargs)
    
    context_kwargs = {
        'viewport': {'width': 1920, 'height': 1080},
        'locale': 'en-US',
        'timezone_id': 'America/New_York',
        'permissions': ['geolocation'],
        'geolocation': {'latitude': 40.7128, 'longitude': -74.0060},
        'color_scheme': 'light'
    }
    if user_agent:
        context_kwargs['user_agent'] = user_agent
    else:
        context_kwargs['user_agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
        
    context = browser.new_context(**context_kwargs)
    
    context.add_init_script('''
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        Object.defineProperty(navigator, 'plugins', {get: () => [
            {name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer'},
            {name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai'},
            {name: 'Native Client', filename: 'internal-nacl-plugin'}
        ]});
        Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
        Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});
        Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => 8});
        Object.defineProperty(navigator, 'deviceMemory', {get: () => 8});
        window.chrome = {
            runtime: {},
            loadTimes: function() {},
            csi: function() {},
            app: {}
        };
        Object.defineProperty(navigator, 'permissions', {
            get: () => ({
                query: () => Promise.resolve({state: 'granted'})
            })
        });
        const originalQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (parameters) => (
            parameters.name === 'notifications' ?
                Promise.resolve({state: Notification.permission}) :
                originalQuery(parameters)
        );
    ''')
    
    page = context.new_page()
    try:
        from playwright_stealth import stealth_sync
        stealth_sync(page)
    except ImportError:
        pass
    return browser, context, page


def get_page_with_flaresolverr(url):
    """
    Get page content using flaresolverr API to bypass Cloudflare
    """
    try:
        flaresolverr_url = os.environ.get('FLARESOLVERR_URL', 'http://localhost:8191/v1')
        
        payload = {
            "cmd": "request.get",
            "url": url,
            "maxTimeout": 60000  # 60 seconds
        }
        
        # Add proxy if available
        if PROXY_URL:
            payload["proxy"] = {
                "http": PROXY_URL,
                "https": PROXY_URL
            }
        
        response = requests.post(flaresolverr_url, json=payload, timeout=90)
        result = response.json()
        
        if result.get('status') == 'ok':
            solution = result.get('solution', {})
            return solution.get('response'), solution.get('url')
        else:
            print(f"FlareSolverr error: {result}")
            return None, None
            
    except Exception as e:
        print(f"Error with FlareSolverr: {e}")
        return None, None


def launch_firefox_browser():
    """
    Launch Firefox browser for scraping
    """
    try:
        from selenium import webdriver
        from selenium.webdriver.firefox.options import Options
        from selenium.webdriver.common.by import By
        
        headless_env = os.environ.get('HEADLESS', 'true').lower() == 'true'
        
        options = Options()
        
        # Add anti-detection arguments
        options.set_preference("dom.webdriver.enabled", False)
        options.set_preference("useAutomationExtension", False)
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        
        # Set headless
        if headless_env:
            options.add_argument("--headless")
        
        # Add proxy if available
        if PROXY_URL:
            options.set_preference("network.proxy.type", 1)
            proxy_parts = PROXY_URL.split(':')
            if len(proxy_parts) == 2:
                options.set_preference("network.proxy.http", proxy_parts[0])
                options.set_preference("network.proxy.http_port", int(proxy_parts[1]))
                options.set_preference("network.proxy.ssl", proxy_parts[0])
                options.set_preference("network.proxy.ssl_port", int(proxy_parts[1]))
        
        # Create driver
        driver = webdriver.Firefox(options=options)
        
        # Set page load timeout
        driver.set_page_load_timeout(60)
        
        return driver
        
    except Exception as e:
        print(f"Error launching Firefox: {e}")
        return None


def launch_undetected_browser():
    """
    Launch undetected-chromedriver for better Cloudflare bypass
    """
    try:
        import undetected_chromedriver as uc
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        
        headless_env = os.environ.get('HEADLESS', 'true').lower() == 'true'
        
        options = uc.ChromeOptions()
        
        # Add anti-detection arguments
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-setuid-sandbox")
        options.add_argument("--disable-infobars")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--start-maximized")
        
        # Set headless
        options.headless = headless_env
        
        # Add proxy if available
        if PROXY_URL:
            options.add_argument(f"--proxy-server={PROXY_URL}")
        
        # Create driver (specify Chrome version 146 to match installed browser)
        driver = uc.Chrome(options=options, version_main=146)
        
        # Set page load timeout
        driver.set_page_load_timeout(60)
        
        return driver
        
    except ImportError:
        print("undetected-chromedriver not installed, falling back to Playwright")
        return None
    except Exception as e:
        print(f"Error launching undetected-chromedriver: {e}")
        return None


def handle_cloudflare_selenium(driver, timeout=60):
    """
    Handle Cloudflare challenge with Selenium/undetected-chromedriver
    """
    import time
    import random
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    
    start = time.time()
    
    while time.time() - start < timeout:
        try:
            title = driver.title
            if "Just a moment" not in title and "Cloudflare" not in title and title.strip():
                print(f"Cloudflare passed! Page title: {title}")
                return True
            
            # Simulate human-like mouse movements
            try:
                from selenium.webdriver.common.action_chains import ActionChains
                actions = ActionChains(driver)
                actions.move_by_offset(random.randint(50, 200), random.randint(50, 200))
                actions.perform()
                time.sleep(random.uniform(0.1, 0.3))
            except Exception:
                pass
            
            # Try to find and click Turnstile checkbox/invisible reCAPTCHA
            try:
                # Look for iframe with Cloudflare challenge
                iframes = driver.find_elements(By.TAG_NAME, "iframe")
                for iframe in iframes:
                    if "challenges.cloudflare.com" in iframe.get_attribute("src") or "turnstile" in iframe.get_attribute("src"):
                        driver.switch_to.frame(iframe)
                        try:
                            # Try various checkbox selectors
                            selectors = [
                                'input[type="checkbox"]',
                                '.mark',
                                '#challenge-stage',
                                'label',
                                'div[class*="checkbox"]',
                                '[role="checkbox"]',
                                '.recaptcha-checkbox-border'
                            ]
                            for selector in selectors:
                                try:
                                    checkbox = driver.find_element(By.CSS_SELECTOR, selector)
                                    if checkbox.is_displayed():
                                        print(f"Clicking Turnstile checkbox with selector: {selector}")
                                        checkbox.click()
                                        time.sleep(random.uniform(1.5, 2.5))
                                        break
                                except Exception:
                                    continue
                        except Exception:
                            pass
                        finally:
                            driver.switch_to.default_content()
                        break
            except Exception:
                pass
            
            time.sleep(random.uniform(1.0, 2.0))
            
        except Exception as e:
            print(f"Error during Cloudflare handling: {e}")
            time.sleep(1)
    
    return False


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
                response = requests.get(iframe_url, headers=headers, proxies=REQUESTS_PROXIES, timeout=30)
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
                response = requests.get(iframe_url, headers=headers, proxies=REQUESTS_PROXIES, timeout=30)
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
        response = requests.get(category_url, headers=headers, proxies=REQUESTS_PROXIES, timeout=30)
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
    Scrape the video title from the page using Firefox (primary), flaresolverr, undetected-chromedriver, or Playwright (fallbacks)
    Returns title like 'Predator Badlands 2025' from the page's <title> tag
    """
    import time
    import re
    
    # Try Firefox first
    print("Using Firefox for title scraping")
    driver = launch_firefox_browser()
    if driver:
        try:
            driver.get(page_url)
            time.sleep(5)
            page_title = driver.title
            title = page_title
            title = re.split(r'\s*[|\-]\s*', title)[0]
            title = re.sub(r'[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]+', ' ', title)
            title = re.sub(r'[\s]+', ' ', title).strip()
            
            if title:
                print(f"Scraped title: {title}")
                driver.quit()
                return title
            
            driver.quit()
        except Exception as e:
            print(f"Error with Firefox: {e}")
            try:
                driver.quit()
            except:
                pass
    
    # Try flaresolverr
    print("Using FlareSolverr for title scraping")
    page_content, final_url = get_page_with_flaresolverr(page_url)
    if page_content:
        try:
            soup = BeautifulSoup(page_content, 'html.parser')
            title_tag = soup.find('title')
            if title_tag:
                page_title = title_tag.text
                title = page_title
                title = re.split(r'\s*[|\-]\s*', title)[0]
                title = re.sub(r'[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]+', ' ', title)
                title = re.sub(r'[\s]+', ' ', title).strip()
                
                if title:
                    print(f"Scraped title: {title}")
                    return title
        except Exception as e:
            print(f"Error parsing FlareSolverr response: {e}")
    
    # Fallback to undetected-chromedriver
    print("Falling back to undetected-chromedriver for title scraping")
    driver = launch_undetected_browser()
    if driver:
        try:
            print("Using undetected-chromedriver for title scraping")
            driver.get(page_url)
            
            if handle_cloudflare_selenium(driver):
                time.sleep(5)
                page_title = driver.title
                title = page_title
                title = re.split(r'\s*[|\-]\s*', title)[0]
                title = re.sub(r'[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]+', ' ', title)
                title = re.sub(r'[\s]+', ' ', title).strip()
                
                if title:
                    print(f"Scraped title: {title}")
                    driver.quit()
                    return title
            
            driver.quit()
        except Exception as e:
            print(f"Error with undetected-chromedriver: {e}")
            try:
                driver.quit()
            except:
                pass
    
    # Fallback to Playwright
    print("Falling back to Playwright for title scraping")
    try:
        from playwright.sync_api import sync_playwright
        
        with sync_playwright() as p:
            browser, context, page = launch_stealth_browser(p, user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
            
            try:
                page.goto(page_url, wait_until='domcontentloaded', timeout=30000)
                handle_cloudflare_challenge(page)
                time.sleep(2)
                
                page_title = page.title()
                title = page_title
                title = re.split(r'\s*[|\-]\s*', title)[0]
                title = re.sub(r'[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]+', ' ', title)
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


def load_processed_movies():
    """Load processed TMDB IDs from JSON file"""
    try:
        if os.path.exists('processed_movies.json'):
            with open('processed_movies.json', 'r') as f:
                data = json.load(f)
                return set(data.get('processed_ids', []))
    except Exception as e:
        print(f"Error loading processed movies: {e}")
    return set()


def save_processed_movie(tmdb_id):
    """Save a processed TMDB ID to JSON file"""
    try:
        processed_ids = load_processed_movies()
        processed_ids.add(str(tmdb_id))
        
        data = {
            'processed_ids': list(processed_ids),
            'last_updated': time.strftime('%Y-%m-%d %H:%M:%S')
        }
        
        with open('processed_movies.json', 'w') as f:
            json.dump(data, f, indent=2)
        
        print(f"Saved TMDB ID {tmdb_id} to processed movies")
    except Exception as e:
        print(f"Error saving processed movie: {e}")


def is_movie_processed(tmdb_id):
    """Check if a TMDB ID has already been processed"""
    processed_ids = load_processed_movies()
    return str(tmdb_id) in processed_ids


def scrape_vidsrc_movie(tmdb_id):
    """
    Extract direct video URL from VidSrc using TMDB ID
    Returns: (title_arabic, video_url)
    Uses Playwright to intercept network requests and extract video URL
    """
    import time
    
    vidsrc_url = f'https://vidsrc.sbs/embed/movie/{tmdb_id}'
    print(f"Extracting video from VidSrc for TMDB ID: {tmdb_id}")
    print(f"URL: {vidsrc_url}")
    
    try:
        from playwright.sync_api import sync_playwright
        
        with sync_playwright() as p:
            browser, context, page = launch_stealth_browser(p, user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
            
            video_url_found = None
            title_found = None
            
            def handle_response(response):
                nonlocal video_url_found
                url = response.url
                ct = response.headers.get('content-type', '')
                
                # Check if this is a video file response
                is_video_ext = any(url.lower().endswith(ext) or (ext + '?') in url.lower() for ext in ['.mp4', '.m3u8', '.ts', '.webm', '.mkv'])
                is_video_ct = 'video' in ct
                
                if is_video_ext or is_video_ct:
                    if 'error' not in url.lower() and 'ping' not in url.lower():
                        if not video_url_found:
                            video_url_found = url
                            print(f"Intercepted video URL: {url}")
            
            page.on('response', handle_response)
            
            try:
                print("Loading VidSrc page with Playwright...")
                page.goto(vidsrc_url, wait_until='domcontentloaded', timeout=30000)
                time.sleep(5)
                
                # Get page title
                page_title = page.title()
                title_found = page_title.split('|')[0].split('-')[0].strip()
                print(f"Found title: {title_found}")
                
                # Wait for network interception
                time.sleep(10)
                
                if video_url_found:
                    print(f"Found video URL via network interception: {video_url_found[:100]}...")
                    return title_found or f"Movie_{tmdb_id}", video_url_found
                
                # Fallback: Try to get page content and look for video URLs in scripts
                page_content = page.content()
                
                # Look for common video URL patterns in scripts
                import re
                video_patterns = [
                    r'(https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*)',
                    r'(https?://[^\s"\'<>]+\.mp4[^\s"\'<>]*)',
                    r'"url"\s*:\s*"(https?://[^"]+)"',
                    r'"file"\s*:\s*"(https?://[^"]+)"',
                    r'"source"\s*:\s*"(https?://[^"]+)"',
                ]
                
                for pattern in video_patterns:
                    matches = re.findall(pattern, page_content)
                    if matches:
                        for match in matches:
                            if match.startswith('http') and 'error' not in match.lower():
                                print(f"Found video URL in page content: {match[:100]}...")
                                return title_found or f"Movie_{tmdb_id}", match
                
                print("Could not extract direct video URL")
                return None, None
                
            finally:
                context.close()
                browser.close()
                
    except Exception as e:
        print(f"Error extracting video URL with Playwright: {e}")
        return None, None


def scrape_video_url(page_url, server_preference='EarnVids'):
    """
    Scrape the video URL from the TV10 page using Firefox (primary), flaresolverr, undetected-chromedriver, or Playwright (fallbacks)
    """
    import time
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    
    # Try Firefox first
    print("Using Firefox for URL scraping")
    driver = launch_firefox_browser()
    if driver:
        try:
            driver.get(page_url)
            time.sleep(5)
            
            # Try to find elements with data-link attribute
            try:
                data_links = driver.find_elements(By.CSS_SELECTOR, '[data-link]')
                if data_links:
                    print(f"Found {len(data_links)} elements with data-link attribute")
                    servers = []
                    for elem in data_links:
                        link = elem.get_attribute('data-link')
                        try:
                            name_elem = elem.find_element(By.TAG_NAME, 'p')
                            name = name_elem.text if name_elem else 'Unknown'
                        except Exception:
                            name = 'Unknown'
                        servers.append({'name': name, 'link': link})
                    
                    print(f"Found {len(servers)} servers:")
                    for server in servers:
                        print(f"  - {server['name']}: {server['link']}")
                    
                    for server in servers:
                        if server_preference.lower() in server['name'].lower():
                            print(f"Selected server: {server['name']}")
                            driver.quit()
                            return server['link']
                    
                    if servers:
                        print(f"Preferred server '{server_preference}' not found, using first available: {servers[0]['name']}")
                        driver.quit()
                        return servers[0]['link']
            except Exception:
                print("No elements with data-link found")
            
            # Get page HTML and look for serversList
            time.sleep(3)
            page_html = driver.page_source
            soup = BeautifulSoup(page_html, 'html.parser')
            
            servers_list = soup.find('ul', class_='serversList')
            if servers_list:
                servers = []
                for li in servers_list.find_all('li', {'data-link': True}):
                    server_name = li.find('p').text if li.find('p') else 'Unknown'
                    server_link = li['data-link']
                    servers.append({'name': server_name, 'link': server_link})
                
                print(f"Found {len(servers)} servers in HTML:")
                for server in servers:
                    print(f"  - {server['name']}: {server['link']}")
                
                for server in servers:
                    if server_preference.lower() in server['name'].lower():
                        print(f"Selected server: {server['name']}")
                        driver.quit()
                        return server['link']
                
                if servers:
                    print(f"Preferred server '{server_preference}' not found, using first available: {servers[0]['name']}")
                    driver.quit()
                    return servers[0]['link']
            else:
                print("No serversList found even after JavaScript execution")
                
                iframe = soup.find('iframe')
                if iframe and iframe.get('src'):
                    iframe_src = iframe['src']
                    print(f"Found iframe: {iframe_src}")
                    driver.quit()
                    return iframe_src
                
                driver.quit()
                return None
            
            driver.quit()
        except Exception as e:
            print(f"Error with Firefox: {e}")
            try:
                driver.quit()
            except:
                pass
    
    # Try flaresolverr
    print("Using FlareSolverr for URL scraping")
    page_content, final_url = get_page_with_flaresolverr(page_url)
    if page_content:
        try:
            soup = BeautifulSoup(page_content, 'html.parser')
            
            data_links = soup.find_all(attrs={'data-link': True})
            if data_links:
                print(f"Found {len(data_links)} elements with data-link attribute")
                servers = []
                for elem in data_links:
                    link = elem.get('data-link')
                    name_elem = elem.find('p')
                    name = name_elem.text if name_elem else 'Unknown'
                    servers.append({'name': name, 'link': link})
                
                print(f"Found {len(servers)} servers:")
                for server in servers:
                    print(f"  - {server['name']}: {server['link']}")
                
                for server in servers:
                    if server_preference.lower() in server['name'].lower():
                        print(f"Selected server: {server['name']}")
                        return server['link']
                
                if servers:
                    print(f"Preferred server '{server_preference}' not found, using first available: {servers[0]['name']}")
                    return servers[0]['link']
            
            servers_list = soup.find('ul', class_='serversList')
            if servers_list:
                servers = []
                for li in servers_list.find_all('li', {'data-link': True}):
                    server_name = li.find('p').text if li.find('p') else 'Unknown'
                    server_link = li['data-link']
                    servers.append({'name': server_name, 'link': server_link})
                
                print(f"Found {len(servers)} servers in HTML:")
                for server in servers:
                    print(f"  - {server['name']}: {server['link']}")
                
                for server in servers:
                    if server_preference.lower() in server['name'].lower():
                        print(f"Selected server: {server['name']}")
                        return server['link']
                
                if servers:
                    print(f"Preferred server '{server_preference}' not found, using first available: {servers[0]['name']}")
                    return servers[0]['link']
            
            iframe = soup.find('iframe')
            if iframe and iframe.get('src'):
                iframe_src = iframe['src']
                print(f"Found iframe: {iframe_src}")
                return iframe_src
            
            print("No video sources found with FlareSolverr")
        except Exception as e:
            print(f"Error parsing FlareSolverr response: {e}")
    
    # Fallback to undetected-chromedriver
    print("Falling back to undetected-chromedriver for URL scraping")
    driver = launch_undetected_browser()
    if driver:
        try:
            print("Using undetected-chromedriver for URL scraping")
            driver.get(page_url)
            
            if handle_cloudflare_selenium(driver):
                time.sleep(5)
                
                current_url = driver.current_url
                print(f"Current URL after Cloudflare: {current_url}")
                
                page_source = driver.page_source
                if 'chrome-error' in page_source.lower() or "This site can't be reached" in page_source:
                    print("On error page, reloading...")
                    driver.refresh()
                    time.sleep(5)
                    page_source = driver.page_source
                    if 'chrome-error' in page_source.lower():
                        print("Still on error page after reload, trying direct navigation...")
                        driver.get(page_url)
                        time.sleep(5)
                
                try:
                    play_button = driver.find_element(By.CSS_SELECTOR, '.fi-play, .fa-play, i[class*="play"]')
                    if play_button:
                        print("Found play button - this is a details page")
                        play_button.click()
                        time.sleep(3)
                        watch_url = driver.current_url
                        print(f"Navigated to watch page: {watch_url}")
                    else:
                        print("No play button found - this might already be a watch page")
                        watch_url = page_url
                except Exception:
                    print("No play button found - this might already be a watch page")
                    watch_url = page_url
                
                try:
                    data_links = driver.find_elements(By.CSS_SELECTOR, '[data-link]')
                    if data_links:
                        print(f"Found {len(data_links)} elements with data-link attribute")
                        servers = []
                        for elem in data_links:
                            link = elem.get_attribute('data-link')
                            try:
                                name_elem = elem.find_element(By.TAG_NAME, 'p')
                                name = name_elem.text if name_elem else 'Unknown'
                            except Exception:
                                name = 'Unknown'
                            servers.append({'name': name, 'link': link})
                        
                        print(f"Found {len(servers)} servers:")
                        for server in servers:
                            print(f"  - {server['name']}: {server['link']}")
                        
                        for server in servers:
                            if server_preference.lower() in server['name'].lower():
                                print(f"Selected server: {server['name']}")
                                driver.quit()
                                return server['link']
                        
                        if servers:
                            print(f"Preferred server '{server_preference}' not found, using first available: {servers[0]['name']}")
                            driver.quit()
                            return servers[0]['link']
                except Exception:
                    print("No elements with data-link found")
                
                time.sleep(3)
                
                # Execute JavaScript to wait for serversList to load
                try:
                    driver.execute_script("""
                        return new Promise((resolve) => {
                            const check = () => {
                                const list = document.querySelector('.serversList');
                                if (list && list.children.length > 0) {
                                    resolve(true);
                                } else {
                                    setTimeout(check, 500);
                                }
                            };
                            check();
                            setTimeout(() => resolve(false), 10000);
                        });
                    """)
                    time.sleep(2)
                except Exception:
                    pass
                
                page_html = driver.page_source
                soup = BeautifulSoup(page_html, 'html.parser')
                
                servers_list = soup.find('ul', class_='serversList')
                if servers_list:
                    servers = []
                    for li in servers_list.find_all('li', {'data-link': True}):
                        server_name = li.find('p').text if li.find('p') else 'Unknown'
                        server_link = li['data-link']
                        servers.append({'name': server_name, 'link': server_link})
                    
                    print(f"Found {len(servers)} servers in HTML:")
                    for server in servers:
                        print(f"  - {server['name']}: {server['link']}")
                    
                    for server in servers:
                        if server_preference.lower() in server['name'].lower():
                            print(f"Selected server: {server['name']}")
                            driver.quit()
                            return server['link']
                    
                    if servers:
                        print(f"Preferred server '{server_preference}' not found, using first available: {servers[0]['name']}")
                        driver.quit()
                        return servers[0]['link']
                else:
                    print("No serversList found even after JavaScript execution")
                    print("Page HTML (first 3000 chars):", page_html[:3000])
                    print("Looking for any li elements with data-link...")
                    all_li = soup.find_all('li')
                    print(f"Found {len(all_li)} li elements total")
                    for li in all_li:
                        if li.get('data-link'):
                            print(f"Found li with data-link: {li.get('data-link')}")
                    
                    iframe = soup.find('iframe')
                    if iframe and iframe.get('src'):
                        iframe_src = iframe['src']
                        print(f"Found iframe: {iframe_src}")
                        driver.quit()
                        return iframe_src
                    
                    video = soup.find('video')
                    if video and video.get('src'):
                        video_src = video['src']
                        print(f"Found video src: {video_src}")
                        driver.quit()
                        return video_src
                    
                    sources = soup.find_all('source')
                    for source in sources:
                        if source.get('src'):
                            source_src = source['src']
                            print(f"Found source: {source_src}")
                            driver.quit()
                            return source_src
                    
                    for elem in soup.find_all(attrs={'data-url': True}):
                        url = elem['data-url']
                        if url.startswith('http'):
                            print(f"Found data-url: {url}")
                            driver.quit()
                            return url
                    
                    for elem in soup.find_all(attrs={'data-src': True}):
                        url = elem['data-src']
                        if url.startswith('http'):
                            print(f"Found data-src: {url}")
                            driver.quit()
                            return url
                    
                    driver.quit()
                    return None
            
            driver.quit()
        except Exception as e:
            print(f"Error with undetected-chromedriver: {e}")
            try:
                driver.quit()
            except:
                pass
    
    # Fallback to Playwright
    print("Falling back to Playwright for URL scraping")
    try:
        from playwright.sync_api import sync_playwright
        
        with sync_playwright() as p:
            browser, context, page = launch_stealth_browser(p, user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36')
            
            try:
                page.goto(page_url, wait_until='domcontentloaded')
                handle_cloudflare_challenge(page)
                time.sleep(2)
                
                play_button = page.query_selector('.fi-play, .fa-play, i[class*="play"]')
                
                if play_button:
                    print("Found play button - this is a details page")
                    play_button.click()
                    time.sleep(3)
                    watch_url = page.url
                    print(f"Navigated to watch page: {watch_url}")
                else:
                    print("No play button found - this might already be a watch page")
                    watch_url = page_url
                
                data_links = page.query_selector_all('[data-link]')
                
                if data_links:
                    print(f"Found {len(data_links)} elements with data-link attribute")
                    servers = []
                    for elem in data_links:
                        link = elem.get_attribute('data-link')
                        name_elem = elem.query_selector('p')
                        name = name_elem.text_content() if name_elem else 'Unknown'
                        servers.append({'name': name, 'link': link})
                    
                    print(f"Found {len(servers)} servers:")
                    for server in servers:
                        print(f"  - {server['name']}: {server['link']}")
                    
                    for server in servers:
                        if server_preference.lower() in server['name'].lower():
                            print(f"Selected server: {server['name']}")
                            return server['link']
                    
                    if servers:
                        print(f"Preferred server '{server_preference}' not found, using first available: {servers[0]['name']}")
                        return servers[0]['link']
                else:
                    print("No elements with data-link found")
                
                page_html = page.content()
                soup = BeautifulSoup(page_html, 'html.parser')
                
                servers_list = soup.find('ul', class_='serversList')
                
                if servers_list:
                    servers = []
                    for li in servers_list.find_all('li', {'data-link': True}):
                        server_name = li.find('p').text if li.find('p') else 'Unknown'
                        server_link = li['data-link']
                        servers.append({'name': server_name, 'link': server_link})
                    
                    print(f"Found {len(servers)} servers in HTML:")
                    for server in servers:
                        print(f"  - {server['name']}: {server['link']}")
                    
                    for server in servers:
                        if server_preference.lower() in server['name'].lower():
                            print(f"Selected server: {server['name']}")
                            return server['link']
                    
                    if servers:
                        print(f"Preferred server '{server_preference}' not found, using first available: {servers[0]['name']}")
                        return servers[0]['link']
                else:
                    print("No serversList found even after JavaScript execution")
                    iframe = soup.find('iframe')
                    if iframe and iframe.get('src'):
                        iframe_src = iframe['src']
                        print(f"Found iframe: {iframe_src}")
                        return iframe_src
                    
                    return None
                    
            finally:
                context.close()
                browser.close()
                
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
            proxies=REQUESTS_PROXIES,
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
        if PROXY_URL:
            ydl_opts['proxy'] = PROXY_URL
        
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
        
        with sync_playwright() as p:
            browser, context, page = launch_stealth_browser(p, user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
            
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
        response = requests.get(page_url, headers=headers, proxies=REQUESTS_PROXIES, timeout=30)
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
            ajax_response = requests.post(ajax_url, data=ajax_data, headers=headers, proxies=REQUESTS_PROXIES, timeout=30)
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
        resp = requests.get(url, proxies=REQUESTS_PROXIES, timeout=15)
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
                if PROXY_URL:
                    ydl_opts['proxy'] = PROXY_URL
                
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
                    proxies=REQUESTS_PROXIES,
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
                        proxies=REQUESTS_PROXIES,
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
        
        upload_response = requests.get(upload_url, proxies=REQUESTS_PROXIES, timeout=60)
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
    tmdb_id = os.environ.get('TMDB_ID')
    source = os.environ.get('SOURCE', 'tv10')  # 'tv10' or 'vidsrc'
    api_key = os.environ.get('EARNVIDS_API_KEY')
    server_preference = os.environ.get('SERVER_PREFERENCE', 'Streamix')
    
    # API key is only required for TV10 source
    if source == 'tv10' and not api_key:
        print("Error: EARNVIDS_API_KEY environment variable not set for TV10 source")
        sys.exit(1)
    
    # Handle VidSrc source
    if source == 'vidsrc':
        if not tmdb_id:
            print("Error: TMDB_ID environment variable not set for VidSrc source")
            sys.exit(1)
        
        if not api_key:
            print("Error: EARNVIDS_API_KEY environment variable not set for VidSrc source")
            sys.exit(1)
        
        print(f"Processing VidSrc movie with TMDB ID: {tmdb_id}")
        
        # Check if already processed
        if is_movie_processed(tmdb_id):
            print(f"TMDB ID {tmdb_id} already processed, skipping")
            sys.exit(0)
        
        # Extract video URL from VidSrc
        video_title, video_url = scrape_vidsrc_movie(tmdb_id)
        
        if not video_url:
            print("Failed to extract video URL from VidSrc")
            sys.exit(1)
        
        print(f"Video URL: {video_url}")
        
        # Upload to DoodStream
        print("Uploading to DoodStream...")
        result = upload_to_earnvids(video_url, api_key, title=video_title or f"Movie_{tmdb_id}")
        
        if result:
            filecode = result.get('filecode')
            print(f"Upload successful! File code: {filecode}")
            
            # Save as processed
            save_processed_movie(tmdb_id)
            
            # Save result to JSON file
            output = {
                'success': True,
                'filecode': filecode,
                'tmdb_id': tmdb_id,
                'title': video_title,
                'original_url': f'https://vidsrc.sbs/embed/movie/{tmdb_id}',
                'video_url': video_url
            }
            
            with open('upload_result.json', 'w') as f:
                json.dump(output, f, indent=2)
            
            print("Result saved to upload_result.json")
        else:
            print("Upload failed")
            sys.exit(1)
        
        return
    
    # Handle TV10 source (original logic)
    if not page_url:
        print("Error: PAGE_URL environment variable not set")
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
