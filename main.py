import datetime
from time import sleep
import playwright.async_api as pw
from playwright.async_api import expect
# from playwright_stealth import stealth_async
from config import settings
from sqlalchemy.engine.result import ScalarResult
import httpx
import os
import hashlib
import datetime as datetime
import metadata as meta
import asyncclick as click
import asyncio
import multiprocessing
from tqdm.asyncio import tqdm
import re
from dateutil.parser import parse
import time
from requests.models import Response
from urllib.parse import urljoin
import m3u8
import subprocess
import tempfile

url = "https://privacy.com.br/"
base_url = "https://privacy.com.br/profile/"
page_url = "https://privacy.com.br/Index?handler=PartialPosts&skip={0}&take={1}&nomePerfil={2}&agendado=false"
following_url = "https://privacy.com.br/Follow?Type=Following"
profile = ""
hdr = ""
filesTotal = 0
savedTotal = 0
postsTotal = 0
linksTotal = 0
metadata = ""
postContent = ''
prevPostId = ''
numPosts = 0
termCols = 0
responses = ''
postBar: tqdm
linkBar: tqdm
downloadBar: tqdm
global barFormat
barFormat = '{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}, {rate_fmt}]'

async def clean_cookies(cookies):
    """Clean and normalize cookie dictionary, handling multiple cookie values."""
    try:
        cleaned = {}
        if isinstance(cookies, httpx.Cookies):
            # Convert httpx.Cookies to dict using direct access
            for name in cookies.keys():
                try:
                    cleaned[name] = cookies.get(name)  # Using get() instead of direct access
                except Exception as cookie_error:
                    print(f"Error getting cookie {name}: {str(cookie_error)}")
                    continue
        else:
            # Handle regular dict
            for name, value in cookies.items():
                try:
                    if isinstance(value, list):
                        cleaned[name] = value[-1]
                    elif isinstance(value, str):
                        cleaned[name] = value
                    elif value is not None:
                        cleaned[name] = str(value)
                except Exception as cookie_error:
                    print(f"Error processing cookie {name}: {str(cookie_error)}")
                    continue
        return cleaned
    except Exception as e:
        print(f"Error in clean_cookies: {str(e)}")
        return {}

async def get_highest_quality_stream(m3u8_url, headers, cookies):
    """Get the highest quality stream URL from m3u8 playlist with debugging."""
    try:
        print("\n=== Starting get_highest_quality_stream ===")
        print(f"URL: {m3u8_url}")
        
        # Debug print incoming cookies
        await debug_print_cookies(cookies, "INCOMING")
        
        # Clean cookies
        cleaned_cookies = {}
        try:
            if isinstance(cookies, httpx.Cookies):
                print("\nCleaning httpx.Cookies object...")
                for name in cookies.keys():
                    value = cookies.get(name)
                    print(f"  Cookie {name} found")
                    cleaned_cookies[name] = value
            else:
                print("\nCleaning dictionary cookies...")
                for name, value in cookies.items():
                    if isinstance(value, list):
                        print(f"  Cookie {name} is a list with {len(value)} values")
                        cleaned_cookies[name] = value[-1]
                    else:
                        cleaned_cookies[name] = value
        except Exception as cookie_error:
            print(f"Error cleaning cookies: {str(cookie_error)}")
            cleaned_cookies = {}
        
        # Debug print cleaned cookies
        print("\nCleaned cookies:")
        for name, value in cleaned_cookies.items():
            print(f"  {name}: {value[:20]}..." if value else f"  {name}: None")
        
        async with httpx.AsyncClient() as client:
            print("\nMaking request with cleaned cookies...")
            try:
                response = await client.get(
                    m3u8_url,
                    headers=headers,
                    cookies=cleaned_cookies,
                    follow_redirects=True
                )
                
                print(f"Response status: {response.status_code}")
                if response.status_code != 200:
                    print(f"HTTP error {response.status_code} for {m3u8_url}")
                    return None

                # Try different encodings
                content = None
                for encoding in ['utf-8', 'latin1', 'cp1252', 'iso-8859-1']:
                    try:
                        content = response.content.decode(encoding)
                        print(f"Successfully decoded with {encoding}")
                        break
                    except UnicodeDecodeError:
                        print(f"Failed to decode with {encoding}")
                        continue

                if content is None:
                    print("Failed to decode m3u8 content with any encoding")
                    return None

                try:
                    print("\nParsing m3u8 content...")
                    m3u8_data = m3u8.loads(content)
                    
                    if m3u8_data.playlists:
                        print(f"Found {len(m3u8_data.playlists)} quality variants")
                        sorted_playlists = sorted(
                            m3u8_data.playlists,
                            key=lambda p: p.stream_info.bandwidth if p.stream_info else 0,
                            reverse=True
                        )
                        if sorted_playlists:
                            highest_quality = sorted_playlists[0].uri
                            print(f"Highest quality variant: {highest_quality}")
                            if not highest_quality.startswith('http'):
                                highest_quality = urljoin(m3u8_url, highest_quality)
                                print(f"Made URL absolute: {highest_quality}")
                            return highest_quality
                    else:
                        print("No quality variants found, using original URL")
                        return m3u8_url
                except Exception as parse_error:
                    print(f"Error parsing m3u8: {str(parse_error)}")
                    return None
                    
            except Exception as request_error:
                print(f"Error making request: {str(request_error)}")
                return None

    except Exception as e:
        print(f"Error in get_highest_quality_stream: {str(e)}")
        import traceback
        traceback.print_exc()
        return None
    finally:
        print("=== Finished get_highest_quality_stream ===\n")

async def download_m3u8_video(m3u8_url, output_path, headers, cookies):
    """Download video from m3u8 playlist using ffmpeg."""
    try:
        # Clean cookies
        cleaned_cookies = await clean_cookies(cookies)
        
        async with httpx.AsyncClient() as client:
            async with client.stream('GET', m3u8_url, headers=headers, cookies=cleaned_cookies, follow_redirects=True) as response:
                if response.status_code != 200:
                    tqdm.write(f"HTTP error {response.status_code} getting m3u8 content")
                    return False

                content = None
                raw_content = await response.aread()
                
                # Try different encodings
                for encoding in ['utf-8', 'latin1', 'cp1252', 'iso-8859-1']:
                    try:
                        content = raw_content.decode(encoding)
                        break
                    except UnicodeDecodeError:
                        continue

                if content is None:
                    tqdm.write("Failed to decode m3u8 content")
                    return False

                # Create a temporary file for the m3u8 content
                m3u8_path = None
                try:
                    # Ensure output directory exists
                    output_dir = os.path.dirname(output_path)
                    os.makedirs(output_dir, exist_ok=True)

                    # Write m3u8 content to temp file
                    with tempfile.NamedTemporaryFile(mode='w', suffix='.m3u8', delete=False, encoding='utf-8') as f:
                        f.write(content)
                        m3u8_path = f.name

                    # Convert cookies to header format
                    cookie_header = '; '.join(f'{k}={v}' for k, v in cleaned_cookies.items() if v is not None)
                    
                    # Update headers with cookies
                    merged_headers = headers.copy()
                    merged_headers['Cookie'] = cookie_header

                    # Convert headers to ffmpeg format
                    headers_str = ''.join([f'{k}: {v}\r\n' for k, v in merged_headers.items()])

                    # Check ffmpeg version and capabilities
                    try:
                        version_process = await asyncio.create_subprocess_exec(
                            'ffmpeg', '-version',
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE
                        )
                        stdout, stderr = await version_process.communicate()
                        # Fix for the backslash issue - use os.linesep instead
                        first_line = stdout.decode().split(os.linesep)[0]
                        tqdm.write(f"FFmpeg version: {first_line}")
                    except Exception as e:
                        tqdm.write(f"FFmpeg version check failed: {str(e)}")
                        return await download_with_youtube_dl(m3u8_url, output_path, headers, cleaned_cookies)

                    # Prepare ffmpeg command with more verbose output
                    command = [
                        'ffmpeg',
                        '-hide_banner',
                        '-loglevel', 'warning',  # Changed from error to warning for more info
                        '-protocol_whitelist', 'file,http,https,tcp,tls,crypto',
                        '-headers', headers_str,
                        '-i', m3u8_url,
                        '-c', 'copy',
                        '-bsf:a', 'aac_adtstoasc',
                        '-stats',
                        '-y',
                        output_path
                    ]

                    # Print command for debugging
                    tqdm.write(f"FFmpeg command: {' '.join(command)}")

                    process = await asyncio.create_subprocess_exec(
                        *command,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE
                    )

                    stdout, stderr = await process.communicate()

                    if process.returncode != 0:
                        error_msg = stderr.decode() if stderr else "Unknown error"
                        tqdm.write(f"FFmpeg error (code {process.returncode}): {error_msg}")
                        
                        # Try with local m3u8 file if remote URL failed
                        tqdm.write("Trying with local m3u8 file...")
                        command[command.index(m3u8_url)] = m3u8_path
                        
                        process = await asyncio.create_subprocess_exec(
                            *command,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE
                        )
                        
                        stdout, stderr = await process.communicate()
                        
                        if process.returncode != 0:
                            error_msg = stderr.decode() if stderr else "Unknown error"
                            tqdm.write(f"FFmpeg error with local file (code {process.returncode}): {error_msg}")
                            return await download_with_youtube_dl(m3u8_url, output_path, headers, cleaned_cookies)

                    if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                        return True
                    else:
                        tqdm.write("FFmpeg completed but output file is empty or missing")
                        return await download_with_youtube_dl(m3u8_url, output_path, headers, cleaned_cookies)

                finally:
                    if m3u8_path:
                        try:
                            os.unlink(m3u8_path)
                        except:
                            pass

    except Exception as e:
        tqdm.write(f"Error in download_m3u8_video: {str(e)}")
        import traceback
        traceback.print_exc()
        return await download_with_youtube_dl(m3u8_url, output_path, headers, cookies)

async def download_with_youtube_dl(url, output_path, headers, cookies):
    """Fallback method using youtube-dl/yt-dlp"""
    try:
        import yt_dlp as youtube_dl
    except ImportError:
        try:
            import youtube_dl
        except ImportError:
            tqdm.write("Neither yt-dlp nor youtube-dl is installed")
            return False

    cookie_header = '; '.join(f'{k}={v}' for k, v in cookies.items() if v is not None)
    
    # Create a temporary cookie file
    cookie_file = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
            for k, v in cookies.items():
                if v is not None:
                    # Write cookies in Netscape format
                    domain = '.privacy.com.br'  # Adjust domain as needed
                    f.write(f"{domain}\tTRUE\t/\tFALSE\t{int(time.time()) + 86400}\t{k}\t{v}\n")
            cookie_file = f.name

        ydl_opts = {
            'format': 'best',
            'outtmpl': output_path,
            'quiet': True,
            'no_warnings': False,  # Changed to show warnings
            'cookiefile': cookie_file,  # Use cookie file instead of headers
            'http_headers': headers,
            'verbose': True  # Added for debugging
        }
        
        try:
            with youtube_dl.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
                if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                    return True
                return False
        except Exception as e:
            tqdm.write(f"youtube-dl error: {str(e)}")
            return False
    finally:
        if cookie_file and os.path.exists(cookie_file):
            try:
                os.unlink(cookie_file)
            except:
                pass

async def try_download_methods(url, output_path, headers, cookies):
    """Try different download methods with debugging"""
    print(f"\n=== Starting download for {output_path} ===")
    print(f"URL: {url}")
    
    # Debug headers
    print("\nHeaders:")
    for key, value in headers.items():
        print(f"  {key}: {value}")
    
    # Debug cookies
    await debug_print_cookies(cookies, "DOWNLOAD")
    
    # Try ffmpeg method first
    print("\nAttempting ffmpeg download...")
    success = await download_m3u8_video(url, output_path, headers, cookies)
    if success:
        print("ffmpeg download successful")
        return True
        
    print("\nffmpeg failed, trying youtube-dl...")
    # Try youtube-dl/yt-dlp as fallback
    success = await download_with_youtube_dl(url, output_path, headers, cookies)
    if success:
        print("youtube-dl download successful")
        return True
        
    print("All download methods failed")
    return False

async def check_url(url, headers, cookies, timeout):
    async with httpx.AsyncClient() as client:
        try:
            response = await client.head(url, headers=headers, cookies=cookies, timeout=timeout)
            return response
        except httpx.ReadTimeout as e:
            return f"ReadTimeout: {e}"
        except httpx.ConnectTimeout as e:
            return f"ConnectTimeout: {e}"
        except httpx.RequestError as e:
            return f"RequestError: {e}"

# Funcao para mostrar os nomes dos perfis e numeros
def display_profiles(profile_names):
    print("Perfis:")
    for i, profile_name in enumerate(profile_names):
        print(f"{i + 1}. {profile_name}")

async def fetch_profiles(page: pw.Page, profile, backlog):
    await page.goto(base_url + profile)
    print(f"Procurando página de postagens do perfil {profile}...")
    global numPosts
    
    try:
        # Wait for page load
        await page.wait_for_load_state('networkidle')
        
        # Look for the post count in the profile tabs section
        posts_text = await page.locator('.font-13 .text-muted.text-menu-profile').first.text_content()
        
        # Extract number from text like "1.615 POSTAGENS"
        posts_count = posts_text.strip().split(' ')[0].replace('.','')
        
        if 'k' in posts_count.lower():
            posts_count = posts_count.lower().replace('k','')
            posts_count = f"{posts_count}000"
            
        numPosts = int(posts_count)
        
    except Exception as e:
        print(f"Error finding posts count: {str(e)}")
        # Set a default value if count cannot be found
        numPosts = 100

    # Rest of the function remains the same
    js = await page.evaluate_handle('navigator')
    ua = await js.evaluate('navigator.userAgent')
    global hdr
    hdr = {
        'Accept': '*/*',
        'Accept-Encoding': 'gzip, deflate, br, zstd',
        'Accept-Language': 'en-US,en;q=0.9,pt-BR;q=0.8,pt;q=0.7',
        'Connection': 'keep-alive',
        'Origin': 'https://privacy.com.br',
        'Referer': 'https://privacy.com.br/',
        'Sec-Fetch-Dest': 'empty',
        'Sec-Fetch-Mode': 'cors',
        'Sec-Fetch-Site': 'same-site',
        'User-Agent': str(ua),
        'sec-ch-ua': '"Microsoft Edge";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Windows"'
    }

    jar = await refreshCookies(page)

    processes = []
    if not backlog:
        print("Buscando postagens com mídia...")
        proc1 = multiprocessing.Process(target=await fetchLinks(page,jar, profile))
        processes.append(proc1)
        proc1.start()
    else:
        print("Atenção: apenas baixando backlog do banco de dados! A página não vai ser varrida agora.")

    global metadata
    if type(metadata) == str:
        openDatabase()
    if metadata.getMediaDownloadCount() > 0:
        proc2 = multiprocessing.Process(target=await downloadLinks(page,jar,profile))
        processes.append(proc2)
        proc2.start()
    else:
        print('Sem mídia para baixar.')
    for proc in processes:
        proc.join()


async def fetchLinks(page: pw.Page, jar, profile):
    # https://privacy.com.br/Index?handler=PartialPosts&skip=10&take=20&nomePerfil=Suelenstodulskii&agendado=false
    skip = 0
    take = 50
    global numPosts
    global postBar
    global linkBar
    global linksTotal
    linkBar = tqdm(total=linksTotal,colour='magenta',dynamic_ncols=True,position=1,desc='Mídia...',delay=5,bar_format=barFormat)
    postBar = tqdm(total=numPosts,colour='yellow',dynamic_ncols=True,position=0,desc='Postagem...',delay=5,bar_format=barFormat)
    while True:
        await page.goto(page_url.format(skip, take, profile),timeout=90000)
        jar = await refreshCookies(page)
        divs = await page.locator('div.card.pb-0.is-post').all()
        links = await page.locator('xpath=//div[contains(@class,"carousel-skeleton-loader")]').all()
        if len(divs) < 1:
            break
        linksTotal += len(links)
        linkBar.total = linksTotal
        await parseLinks(divs, profile)
        skip += take
    postBar.close()
    linkBar.close()
    global metadata
    global postsTotal
    print(f"{postsTotal} postagens com texto e mídia, {metadata.getMediaCount()} mídias encontradas. Baixando {metadata.getMediaDownloadCount()} mídias.")
    
async def parseLinks(divs, profile):
    openDatabase()
    mediaCount = 0
    for d in divs:
        privacy_web_mediahub_carousel = await d.locator('//privacy-web-mediahub-carousel').all()
        if privacy_web_mediahub_carousel:
            for carousel_element in privacy_web_mediahub_carousel:
                carousel = await carousel_element.evaluate('(element) => element.getAttribute("medias")')                                    
            postTag = d.get_by_role('paragraph')
            id_div = await d.locator('css=div.post-view-full').get_attribute('id')
            postId = id_div.replace('Postagem','')
            global prevPostId
            if prevPostId != postId:
                global termCols
                if termCols < 80:
                    desc = f"P {truncate_middle(postId,12)}"
                else:
                    desc = f"Post  {postId}"
                postBar.set_description(desc)
                postBar.update()
                global postContent
                try:
                    await expect(postTag).to_have_count(count=1,timeout=2)
                    postContent = await postTag.text_content()
                    postContent = postContent.strip()
                    postinfo = {
                        'post_id': postId,
                        'post_text': postContent,
                    }
                    metadata.savePost(postinfo)
                    global numPosts
                    global postsTotal
                    postsTotal += 1
                except AssertionError:
                    pass
        
            global linkBar, linkBarD
            await asyncio.sleep(0)

            # Extraindo as midias do carrosel
            matches = re.findall(r'\{"isLocked":false,"mediaId":".*?","type":"(.*?)","url":"(.*?)".*?\}', carousel)

            # Construindo um dicionario para pegar o media_type de cada arquivo
            media_info = {media_link: media_type for media_type, media_link in matches if media_link and media_type}

            for media_link, media_type in media_info.items():            
                if prevPostId != postId:
                    mediaCount = 1
                    prevPostId = postId
                imgHash = hashlib.md5(str(media_link).encode('utf-8')).hexdigest()
                
                # Updated media type detection with m3u8 handling
                if ".m3u8" in media_link:
                    filename = postId + '-' + str(mediaCount).rjust(3,'0') + '.mp4'  # Save m3u8 streams as mp4
                    media_type = 'video'
                elif "mp4" in media_link:
                    filename = postId + '-' + str(mediaCount).rjust(3,'0') + '.mp4'
                    media_type = 'video'
                else:
                    filename = postId + '-' + str(mediaCount).rjust(3,'0') + '.jpg'
                    media_type = 'image'
                    
                filepath = os.path.join(settings.downloaddir, profile, media_type)
                os.makedirs(name=filepath, exist_ok=True)
                mediainfo = {
                    'media_id': imgHash,
                    'post_id': postId,
                    'link': media_link,
                    'inner_link': media_link,
                    'directory': filepath,
                    'filename': filename,
                    'size': 0,
                    'media_type': media_type,
                    'downloaded': False,
                    'created_at': datetime.datetime.now()
                }
                if not metadata.checkSaved(mediainfo):
                    metadata.saveLinks(mediainfo)
                    
                if termCols < 80:
                    desc = f"M {truncate_middle(filename,12)}"
                else:
                    desc = f"Mídia {filename}"
                linkBar.set_description(desc)
                linkBar.update()
                mediaCount += 1
                await asyncio.sleep(0)
        
async def downloadLinks(drv, cookiejar, profile):
    profilePath = os.path.join(settings.downloaddir, profile)
    os.makedirs(name=profilePath, exist_ok=True)
    global metadata
    mediaCount = 0
    mediastoDownload = metadata.getMediaDownload()
    medias = asyncio.Queue()

    async with httpx.AsyncClient() as client:
        tasks = []
        for m in mediastoDownload:
            inner_link = m.inner_link
            if inner_link:
                timeout = httpx.Timeout(10.0, read=60.0)
                task = asyncio.create_task(check_url(inner_link, hdr, cookiejar, timeout))
                tasks.append(task)
        
        print("Verificando cabeçalhos de mídia...")
        global responses
        responses = await tqdm.gather(*tasks,colour='blue',dynamic_ncols=True,bar_format=barFormat)
        tasks.clear()
        
        async def check(media, response):
            # filepath = os.path.join(media.directory, media.filename)
            response_status = False
            if response.status_code == 200:
                response_status = True
                if response.headers.get('content-length'):
                    media.size = int(response.headers.get('content-length'))
            # if os.path.exists(filepath):
            #     if os.path.getsize(filepath) == response.headers.get('content-length'):
            #         media.downloaded = True
            #     else:
            #         return media
            # else:
                if response_status:
                    return media
                
        mediastoDownload = metadata.getMediaDownload()
        async def checkLink(m,r):
            while not m.empty():
                media = await m.get()
                temp_response = [
                    response
                    for response in r
                    if isinstance(response, Response) and response and str(response.url) == media.link
                    ]
                if temp_response:
                    temp_response = temp_response[0]
                    ret = await check(media, temp_response)
                    mdBar.update()
                    medias.task_done()
                    return ret

        print('Lendo metadados...')
        # for media in tqdm(mediastoDownload,colour='blue',dynamic_ncols=True,bar_format=barFormat):
        #     temp_response = [
        #         response
        #         for response in responses
        #         if response and str(response.url) == media.link
        #         ]
        #     if temp_response:
        #         temp_response = temp_response[0]
        #         task = check(media, temp_response)
        #         tasks.append(task)
        # results = await tqdm.gather(*tasks,colour='blue',dynamic_ncols=True,bar_format=barFormat)
        metadata.session.commit()
        # tasks.clear()
    
        global mdBar
        total = metadata.getMediaDownloadCount()
        with tqdm(total=total,colour='blue',dynamic_ncols=True,bar_format=barFormat) as mdBar:
            results = await asyncio.gather(*([retrieveLinks(mediastoDownload,medias)] + [checkLink(medias,responses) for _ in range(total)]))
            medialist = [x for x in results if x]
            mdBar.close()

    mediastoDownload = metadata.getMediaDownload()
    global downloadBar
    with tqdm(dynamic_ncols=True,colour='green',bar_format=barFormat,unit='B',unit_scale=True,miniters=1) as downloadBar:
        total = 0
        for x in medialist:
            total += int(x.size)
        downloadBar.total = int(total*1.00001)
        global savedTotal
        if (savedTotal>0 and savedTotal % 200 == 0) or len(medialist) > 1000:
            await drv.reload()
            cookiejar = await refreshCookies(drv)
        print(" Baixando mídia...")
        # m.filename == 'b3a4b631-ab8e-419a-b000-5e0d0c4e43a7-002.jpg'
        # perdendo o link de um arquivo, ficando com a url vazia mas no resto está vindo correto a url
        await asyncio.gather(*([retrieveLinks(mediastoDownload,medias)] + [requestLink(medias, cookiejar) for _ in range(4)]))

async def retrieveLinks(mediastoDownload, medias: asyncio.Queue()):
    for m in mediastoDownload:
        await medias.put(m)

async def requestLink(medias, cookiejar):
    global filesTotal, downloadBar, termCols, responses, savedTotal
    
    while not medias.empty():
        media = await medias.get()
        if termCols < 80:
            desc = truncate_middle(media.filename,12)
        else:
            desc = media.filename
        downloadBar.set_description(f"{desc}")
        downloadBar.update()
        mediainfo = {}
        
        url = media.link
        output_path = os.path.join(media.directory, media.filename)
        
        # Handle m3u8 files
        if ".m3u8" in url:
            try:
                # Remove problematic encodings from headers
                modified_headers = hdr.copy()
                modified_headers['Accept-Encoding'] = 'gzip, deflate'  # Remove br and zstd
                
                # Get highest quality stream
                stream_url = await get_highest_quality_stream(url, modified_headers, cookiejar)
                if not stream_url:
                    tqdm.write(f"Failed to get stream URL for {media.filename}")
                    medias.task_done()
                    continue
                
                # If stream_url is relative, make it absolute
                if not stream_url.startswith('http'):
                    stream_url = urljoin(url, stream_url)
                
                # Create output directory if it doesn't exist
                os.makedirs(os.path.dirname(output_path), exist_ok=True)

                # Convert cookies to netscape format for youtube-dl fallback
                cookie_file = None
                try:
                    with tempfile.NamedTemporaryFile(mode='w', delete=False, encoding='utf-8') as f:
                        f.write("# Netscape HTTP Cookie File\n")
                        f.write("# https://curl.haxx.se/rfc/cookie_spec.html\n")
                        f.write("# This file was generated by yt-dlp\n\n")
                        for name, value in cookiejar.items():
                            if value is not None:
                                f.write(f".privacy.com.br\tTRUE\t/\tFALSE\t{int(time.time()) + 86400}\t{name}\t{value}\n")
                        cookie_file = f.name

                    # Prepare ffmpeg command
                    cookie_header = '; '.join(f'{k}={v}' for k, v in cookiejar.items() if v is not None)
                    ffmpeg_headers = {
                        'Cookie': cookie_header,
                        'User-Agent': modified_headers['User-Agent'],
                        'Referer': 'https://privacy.com.br/'
                    }
                    headers_str = ''.join([f'{k}: {v}\r\n' for k, v in ffmpeg_headers.items()])

                    command = [
                        'ffmpeg',
                        '-hide_banner',
                        '-loglevel', 'warning',
                        '-protocol_whitelist', 'file,http,https,tcp,tls,crypto',
                        '-headers', headers_str,
                        '-i', stream_url,
                        '-c', 'copy',
                        '-bsf:a', 'aac_adtstoasc',
                        '-y',
                        output_path
                    ]

                    # Try ffmpeg first
                    try:
                        process = await asyncio.create_subprocess_exec(
                            *command,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE
                        )
                        stdout, stderr = await process.communicate()

                        if process.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                            saved = os.path.getsize(output_path)
                            filesTotal += saved
                            downloadBar.update(saved)
                            
                            mediainfo['media_id'] = media.media_id
                            mediainfo['size'] = saved
                            mediainfo['created_at'] = datetime.datetime.now()
                            metadata.markDownloaded(mediainfo)
                            savedTotal += 1
                            continue

                    except Exception as e:
                        tqdm.write(f"FFmpeg error: {str(e)}")

                    # If ffmpeg fails, try yt-dlp
                    try:
                        import yt_dlp
                        ydl_opts = {
                            'format': 'best',
                            'outtmpl': output_path,
                            'quiet': True,
                            'cookiefile': cookie_file,
                            'http_headers': modified_headers
                        }
                        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                            ydl.download([stream_url])
                            
                            if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                                saved = os.path.getsize(output_path)
                                filesTotal += saved
                                downloadBar.update(saved)
                                
                                mediainfo['media_id'] = media.media_id
                                mediainfo['size'] = saved
                                mediainfo['created_at'] = datetime.datetime.now()
                                metadata.markDownloaded(mediainfo)
                                savedTotal += 1

                    except Exception as e:
                        tqdm.write(f"yt-dlp error: {str(e)}")

                finally:
                    if cookie_file and os.path.exists(cookie_file):
                        try:
                            os.unlink(cookie_file)
                        except:
                            pass

            except Exception as e:
                tqdm.write(f"Error processing m3u8 file {media.filename}: {str(e)}")
            finally:
                medias.task_done()
                continue

        # Rest of your existing code for handling regular files...
        client = httpx.AsyncClient()
        timeout = httpx.Timeout(10.0, read=60.0)
        temp_response = [
            response
            for response in responses
            if isinstance(response, Response) and response and str(response.url) == media.link
        ]
        
        if temp_response:
            temp_response = temp_response[0]
            if temp_response.status_code == 413:
                url = media.inner_link

        async with client.stream('GET', url=url, headers=hdr, cookies=cookiejar, timeout=timeout) as req:
            if req.status_code == 200:
                saved = 0
                if int(req.headers['Content-Length']) < 10000000: #10MB
                    await req.aread()
                    with open(output_path, 'wb') as download:
                        saved = download.write(req.content)
                    filesTotal += saved
                    downloadBar.update(saved)
                else:
                    with open(output_path, 'wb') as download:
                        async for chunk in req.aiter_bytes():
                            f = download.write(chunk)
                            saved += f
                            downloadBar.update(f)
                        filesTotal += f

                if saved > 0:
                    date = parse(req.headers['last-modified'])
                    mtime = time.mktime(date.timetuple())
                    os.utime(output_path, (mtime, mtime))
                    mediainfo['media_id'] = media.media_id
                    mediainfo['size'] = os.path.getsize(output_path)
                    mediainfo['created_at'] = date
                    metadata.markDownloaded(mediainfo)
                    savedTotal += 1
            else:
                tqdm.write(f"Download de {media.filename} falhou, HTTP erro {req.status_code}")
            medias.task_done()

async def debug_print_cookies(cookies, label=""):
    """Helper function to print cookie information"""
    print(f"\n=== Debug Cookies {label} ===")
    print(f"Cookie type: {type(cookies)}")
    
    if isinstance(cookies, httpx.Cookies):
        print("Cookie jar contents:")
        for key in cookies.keys():
            value = cookies.get(key)
            print(f"  {key}: {value}")
    elif isinstance(cookies, dict):
        print("Cookie dict contents:")
        for key, value in cookies.items():
            print(f"  {key}: {value}")
    else:
        print(f"Unknown cookie type: {type(cookies)}")
    print("=" * 50)

async def refreshCookies(driver: pw.Page):
    """Get cookies from browser with detailed debugging"""
    print("\n=== Starting refreshCookies ===")
    
    # Get all cookies from the browser
    browser_cookies = await driver.context.cookies()
    print(f"\nRaw browser cookies count: {len(browser_cookies)}")
    
    # Create a dictionary to store cookies by their full identity (name + domain + path)
    cookie_dict = {}
    cookie_counts = {}
    
    # First pass: collect cookies and count them
    print("\nProcessing cookies:")
    for cookie in browser_cookies:
        name = cookie['name']
        domain = cookie['domain']
        path = cookie['path']
        
        # Create a unique key for this cookie
        cookie_key = f"{name}:{domain}:{path}"
        cookie_dict[cookie_key] = cookie
        
        # Count cookies by name
        cookie_counts[name] = cookie_counts.get(name, 0) + 1
        
        print(f"Cookie: {name}")
        print(f"  Domain: {domain}")
        print(f"  Path: {path}")
        print(f"  Value: {cookie['value'][:20]}...")

    print("\nCookie counts:")
    for name, count in cookie_counts.items():
        print(f"  {name}: {count} times")

    # Create new cookie jar
    jar = httpx.Cookies()
    
    # Process cookies by name
    print("\nSetting cookies:")
    for cookie_key, cookie in cookie_dict.items():
        name = cookie['name']
        value = cookie['value']
        domain = cookie['domain']
        path = cookie['path']
        
        try:
            # Special handling for __cf_bm cookies
            if name == '__cf_bm':
                # Only use the cookie for .privacy.com.br domain if it exists
                if domain == '.privacy.com.br':
                    print(f"Setting {name} cookie for {domain}")
                    jar.set(name, value, domain=domain, path=path)
                else:
                    print(f"Skipping {name} cookie for {domain}")
            else:
                print(f"Setting {name} cookie for {domain}")
                jar.set(name, value, domain=domain, path=path)
        except Exception as e:
            print(f"Error setting cookie {name}: {str(e)}")
            continue

    # Verify final cookie jar
    print("\nFinal cookie jar contents:")
    try:
        for name in jar.keys():
            try:
                # Use a try block for each cookie access
                value = jar.get(name)
                print(f"  {name}: {value[:20]}..." if value else f"  {name}: None")
            except httpx.CookieConflict:
                print(f"  {name}: [Multiple values - conflict]")
            except Exception as e:
                print(f"  {name}: Error getting value - {str(e)}")
    except Exception as e:
        print(f"Error listing cookies: {str(e)}")

    print("=== Finished refreshCookies ===\n")
    return jar

#DISABLE IMAGES ON FIREFOX
# def disable_images(driver):
#     driver.get("about:config")
#     warningButton = WebDriverWait(driver, timeout=30).until(lambda d: d.find_element(By.ID,"warningButton"))
#     warningButton.click()
#     searchArea = WebDriverWait(driver, timeout=30).until(lambda d: d.find_element(By.ID,"about-config-search"))
#     searchArea.send_keys("permissions.default.image")
#     editButton = WebDriverWait(driver, timeout=30).until(lambda d: d.find_element(By.XPATH,"/html/body/table/tr[1]/td[2]/button"))
#     editButton.click()
#     editArea = WebDriverWait(driver, timeout=30).until(lambda d: d.find_element(By.XPATH,"/html/body/table/tr[1]/td[1]/form/input"))
#     editArea.send_keys("2")
#     saveButton = WebDriverWait(driver, timeout=30).until(lambda d: d.find_element(By.XPATH,"/html/body/table/tr[1]/td[2]/button"))
#     saveButton.click()

def openDatabase():
    profilePath = os.path.join(settings.downloaddir, profile)
    os.makedirs(name=profilePath, exist_ok=True)
    global metadata
    metadata = meta.metadata(profilePath)
    metadata.openDatabase()

def truncate_middle(s, n):
    if len(s) <= n:
        # string is already short-enough
        return s
    # half of the size, minus the 3 .'s
    n_2 = int(n / 2 - 3)
    # whatever's left
    n_1 = int(n - n_2 - 3)
    return '{0}...{1}'.format(s[:n_1], s[-n_2:])


@click.command()
@click.option(
    '--backlog',
    '-b',
    is_flag=True,
    default=False,
    help='Baixa apenas o "backlog" de mídias novas no DB, sem varrer a página'
    )
async def main(backlog):
    """Baixa toda a mídia seguida, aceita todos os perfis ou cada um individual."""
    global termCols
    termCols = os.get_terminal_size().columns
    async with pw.async_playwright() as p:
        global profile
        browser = await p.chromium.launch(
            args=['--blink-settings=imagesEnabled=false'],
            headless=False
        )
        print("Abrindo página de login...")
        page = await browser.new_page()
        await page.goto('about:blank')
        sleep(2)
        await page.goto(url)
        
        user = page.get_by_placeholder('E-mail')
        await expect(user).to_be_editable()
        await user.type(settings.user)
        sleep(1)
        
        pwd = page.get_by_placeholder('Senha')
        await expect(pwd).to_be_editable()
        await pwd.type(settings.pwd)
        sleep(1)
        
        # Updated button selector
        btn = page.locator('div.base-button.primary:has-text("Entrar")')
        await btn.click()
        
        print("Aguardando autenticação...")
        # Updated search input selector
        await expect(page.locator('#termSearchHeader[placeholder="Procurar usuários"]')).to_be_visible(timeout=90000)
        sleep(5)
        
        #entrando na pagina do perfil para verificar os perfis seguidos
        await page.goto(following_url)
        sleep(5)

        profile_links = await page.locator('a[href^="https://privacy.com.br/profile/"]').evaluate_all('(links) => links.map(link => link.href)')
        
        # Extrai o nome dos perfis da pagina de seguindo do usuario
        profile_names_set = set(link.split('/')[-1] for link in profile_links)
        profile_names = list(profile_names_set)
        
        display_profiles(profile_names)

        # Pede para o utilizador escolher um perfil
        selection = input("Entre com o numero do perfil correspondente que queira baixar. (0 para todos os perfis): ")        
        
        if selection == "0":
            for profile in profile_names:
                await fetch_profiles(page, profile, backlog)
        elif selection.isdigit() and 0 < int(selection) <= len(profile_names):
            selected_profile = profile_names[int(selection) - 1]
            await fetch_profiles(page, selected_profile, backlog)
        
        await browser.close()
        print('Encerrado.')


if __name__ == "__main__":
    asyncio.run(main())
