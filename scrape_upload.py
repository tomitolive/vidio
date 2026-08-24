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
        This is a placeholder - implement actual decoding logic based on the specific service
        """
        # For vidaraa.cc and similar services, the iframe URL might need processing
        # This is where you would add the specific decoding logic
        
        # For now, return the iframe URL as-is (assuming it's a direct video URL)
        # In production, you might need to:
        # 1. Fetch the iframe page
        # 2. Extract the actual video source from the page
        # 3. Return the direct video URL
        
        return iframe_url


def scrape_video_url(page_url, server_preference='EarnVids'):
    """
    Scrape the video URL from the TV10 page
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    
    try:
        response = requests.get(page_url, headers=headers, timeout=30)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # First try to find serversList
        servers_list = soup.find('ul', class_='serversList')
        
        if servers_list:
            # Extract all server links
            servers = []
            for li in servers_list.find_all('li', {'data-link': True}):
                server_name = li.find('p').text if li.find('p') else 'Unknown'
                server_link = li['data-link']
                servers.append({'name': server_name, 'link': server_link})
            
            print(f"Found {len(servers)} servers:")
            for server in servers:
                print(f"  - {server['name']}: {server['link']}")
            
            # Try to find preferred server
            for server in servers:
                if server_preference.lower() in server['name'].lower():
                    print(f"Selected server: {server['name']}")
                    return server['link']
            
            # If preferred not found, use first one
            if servers:
                print(f"Using first available server: {servers[0]['name']}")
                return servers[0]['link']
        
        # Fallback: try to find iframe with video
        iframe = soup.find('iframe')
        
        if not iframe:
            # Try to find iframe with specific attributes
            iframe = soup.find('iframe', {'src': True})
        
        if iframe and iframe.get('src'):
            iframe_src = iframe['src']
            # Make sure it's absolute URL
            if not iframe_src.startswith('http'):
                iframe_src = urljoin(page_url, iframe_src)
            
            return iframe_src
        else:
            print("No iframe or serversList found on the page")
            return None
            
    except Exception as e:
        print(f"Error scraping page: {e}")
        return None


def upload_to_earnvids(video_url, api_key):
    """
    Upload video to earnvidsapi using Upload by URL API
    """
    upload_url = "https://earnvidsapi.com/api/upload/url"
    
    params = {
        'key': api_key,
        'url': video_url
    }
    
    try:
        response = requests.get(upload_url, params=params, timeout=30)
        response.raise_for_status()
        
        data = response.json()
        
        if data.get('status') == 200:
            return data.get('result')
        else:
            print(f"API Error: {data.get('msg')}")
            return None
            
    except Exception as e:
        print(f"Error uploading to earnvids: {e}")
        return None


def main():
    # Get parameters from environment or command line
    page_url = os.environ.get('PAGE_URL')
    api_key = os.environ.get('EARNVIDS_API_KEY')
    
    if not page_url:
        print("Error: PAGE_URL environment variable not set")
        sys.exit(1)
    
    if not api_key:
        print("Error: EARNVIDS_API_KEY environment variable not set")
        sys.exit(1)
    
    print(f"Scraping URL: {page_url}")
    
    # Step 1: Scrape the iframe URL
    iframe_url = scrape_video_url(page_url)
    
    if not iframe_url:
        print("Failed to scrape video URL")
        sys.exit(1)
    
    print(f"Found iframe URL: {iframe_url}")
    
    # Step 2: Decode the actual video URL
    decoder = ServersList()
    video_url = decoder.decode_url(iframe_url)
    
    print(f"Video URL: {video_url}")
    
    # Step 3: Upload to earnvids
    print("Uploading to earnvids...")
    result = upload_to_earnvids(video_url, api_key)
    
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
