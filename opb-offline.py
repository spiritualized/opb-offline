import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
from datetime import datetime

import requests
import bs4
from retry import retry


class VideoError(Exception):
    def __init__(self, msg: str):
        self.error_msg = msg
        super().__init__(self.error_msg)


class ShowDoesNotExistError(Exception):
    def __init__(self, show_key: str):
        self.error_msg = f'Show with URL key "{show_key}" does not exist'
        super().__init__(self.error_msg)


class Show:
    def __init__(self, show_key: str):
        self.show_key = show_key
        self.seasons = []
        self.has_specials = False

        self._populate()

    def _populate(self):
        url = f'https://watch.opb.org/show/{self.show_key}/episodes/'
        html = requests.get(url)

        if html.status_code == 404:
            raise ShowDoesNotExistError(self.show_key)

        page = bs4.BeautifulSoup(html.content, 'html5lib')

        if page.find('select', attrs={'data-content-type': 'episodes'}):
            for curr_season in page.find('select', attrs={'data-content-type': 'episodes'}).find_all('option'):
                self.seasons.insert(0, int(curr_season['value']))
        else:
            self.seasons.append(1)

        url = f'https://watch.opb.org/show/{self.show_key}/specials/'
        html = requests.get(url)

        page = bs4.BeautifulSoup(html.content, 'html5lib')

        if page.find('div', class_='video-catalog__item'):
            self.has_specials = True


class Season:
    def __init__(self, url: str):
        self.url = url
        self.episodes = []
        self.num = None
        self.additional_group = None

        self._populate()

    def _populate(self):
        html = requests.get(self.url, allow_redirects=False)

        if html.status_code == 302:
            raise VideoError(f'Season does not exist')

        page = bs4.BeautifulSoup(html.content, 'html5lib')

        self.title = str(page.find('a', class_='breadcrumbs__link').contents[0])

        season_text = str(page.find('h1', class_='video-catalog__title').contents[0]).strip()

        # parse out the season number, unless it's a non-season group
        if re.search(r'Season (\d{1,3})', season_text):
            self.num = int(re.search(r'Season (\d{1,3})', season_text).group(1))
        else:
            self.additional_group = season_text

        for episode in page.find_all('div', class_='video-catalog__item'):
            partial_url = episode.find('a', class_='video-summary__video-title-link')['href']
            url = f'https://watch.opb.org{partial_url}'
            episode_title = str(episode.find('a', class_='video-summary__video-title-link').contents[0]).strip()

            info = episode.find('p', class_='video-summary__meta-data').contents[0]
            date = None
            additional_group = None

            # hack for erroneously prepended season numbers
            if re.search(r'S(\d{1,2}) Ep(\d{1,2}) \| ', info):
                episode_num = int(re.search(r'S(\d{1,2}) Ep(\d{1,2}) \| ', info).group(2))
            elif re.search(r'S(\d{1,2}) Ep(\d{3,4})(( \| )|\n)', info):
                episode_num = int(re.search(r'S(\d{1,2}) Ep(\d{3,4})(( \| )|\n)', info).group(2)[-2:])
            elif re.search(r' (\d{2}/\d{2}/\d{4}) ', info):  # No episode number, but an original air date
                episode_num = None
                date = datetime.strptime(re.search(r' (\d{2}/\d{2}/\d{4}) ', info).group(1), '%m/%d/%Y')
            else:
                episode_num = None
                additional_group = re.search(r'(\w+)(( \| )|\n)', info).group(1)

            episode = Episode(self, episode_title, url, episode_num, date, additional_group)
            self.episodes.append(episode)

        self.episodes = sorted(self.episodes)

    def get_normalized_name(self) -> str:
        return self.title.replace(' ', '.').replace(';', '')

    def get_folder_name(self) -> str:
        group = f'-{GROUP}' if GROUP else ''
        if self.num:
            return f'{self.get_normalized_name()}.S{self.num:02d}.WEB.h264.AAC{group}'
        else:
            return f'{self.get_normalized_name()}.{self.additional_group}.WEB.h264.AAC{group}'


class Episode:
    def __init__(self, season: Season, title: str, url: str, num: int, date: datetime, additional_group: str):
        self.season = season
        self.num = num
        self.date = date
        self.additional_group = additional_group
        self.title = title
        self.url = url
        self.resolution = None
        self.video_codec = None
        self.audio_channels = None
        self.audio_codec = None

    def get_video_url(self) -> str:
        html = requests.get(self.url)
        soup = bs4.BeautifulSoup(html.content, 'html5lib')

        inline_script = str(soup.find('script', type='text/javascript'))
        video_id = int(re.search(r"id: '(\d*)',", inline_script).group(1))

        url_1 = f'https://player.pbs.org/stationplayer/{video_id}/?callsign=KOPB' \
                f'&parentURL={urllib.parse.quote_plus(self.url)}' \
                f'&unsafeDisableUpsellHref=true&unsafePostMessages=true'

        page = requests.get(url_1)
        soup = bs4.BeautifulSoup(page.content, 'html5lib')

        if soup.find('p', class_='error-message'):
            raise VideoError(str(soup.find('p', class_='error-message').contents[0]).strip())

        script = soup.find_all('script', type=None, src=None,
                               text=lambda t: t and 'window.contextBridge' in t)[0].contents[0]
        if not re.search(r'"encodings": \["https://urs.pbs.org/redirect/(\w*)/"', script):
            raise VideoError('Video redirect token not found')

        redirect_token = re.search(r'"encodings": \["https://urs.pbs.org/redirect/(\w*)/"', script).group(1)

        url_2 = f'https://urs.pbs.org/redirect/{redirect_token}/?format=jsonp&callback=__whatever'

        page = requests.get(url_2)
        video_url = json.loads(re.search(r'__whatever\((.*)\)', str(page.content)).group(1))['url']

        return video_url

    def get_normalized_title(self) -> str:
        dotted_episode_title = self.title.replace(' ', '.').replace(';', '').replace(',', '').replace('/', '.')\
            .replace('\\', '.').replace("'", '').replace('"', '').replace('-', '.').replace('?', '').replace(':', '')\
            .replace('|', '.')
        return re.sub(r'\.+', '.', dotted_episode_title)

    def get_filename(self) -> str:
        group = f'-{GROUP}' if GROUP else ''
        if self.season.num and self.num:
            return f'{self.season.get_normalized_name()}.S{self.season.num:02d}E{self.num:02d}.' \
                   f'{self.get_normalized_title()}.{self.resolution}p.WEB.{self.video_codec}.{self.audio_codec}.' \
                   f'{self.audio_channels}{group}.mp4'
        elif self.season.num and self.date:
            date_str = datetime.strftime(self.date, '%Y-%m-%d')
            return f'{self.season.get_normalized_name()}.S{self.season.num:02d}.{date_str}.' \
                   f'{self.get_normalized_title()}.{self.resolution}p.WEB.{self.video_codec}.{self.audio_codec}.' \
                   f'{self.audio_channels}{group}.mp4'
        else:
            return f'{self.season.get_normalized_name()}.{self.additional_group}.' \
                   f'{self.get_normalized_title()}.{self.resolution}p.WEB.{self.video_codec}.{self.audio_codec}.' \
                   f'{self.audio_channels}{group}.mp4'

    def get_dupe_check_regex(self) -> str:
        group = f'-{GROUP}' if GROUP else ''
        if self.season.num and self.num:
            return f'{self.season.get_normalized_name()}.S{self.season.num:02d}E{self.num:02d}.' \
                   f'{self.get_normalized_title()}.' + r'\d{2,4}' + r'p.WEB.\w+.\w+.\d.\d' + f'{group}.mp4'
        elif self.season.num and self.date:
            date_str = datetime.strftime(self.date, '%Y-%m-%d')
            return f'{self.season.get_normalized_name()}.S{self.season.num:02d}.{date_str}.' \
                   f'{self.get_normalized_title()}.' + r'\d{2,4}' + r'p.WEB.\w+.\w+.\d.\d' + f'{group}.mp4'
        else:
            return f'{self.season.get_normalized_name()}.{self.additional_group}.' \
                   f'{self.get_normalized_title()}.' + r'\d{2,4}' + r'p.WEB.\w+.\w+.\d.\d' + f'{group}.mp4'

    def populate_attributes_from_file(self, path: str) -> None:
        self.resolution = get_video_height(path)
        self.video_codec = get_video_codec(path)
        self.audio_codec = get_audio_codec(path)
        self.audio_channels = get_audio_channels(path)

    def __lt__(self, other) -> bool:
        if self.num and other.num:
            return self.num < other.num
        return False


def get_video_height(path: str) -> int:
    """Retrieve the video's height, in pixels, using FFMPEG"""

    resolution = subprocess.check_output(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries',
                                          'stream=width,height', '-of', 'csv=p=0', path])
    return int(str(resolution.strip())[1:-1].split(',')[1])


def get_video_codec(path: str) -> str:
    """Retrieve the video codec using FFMPEG"""

    return subprocess.check_output(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries',
                                    'stream=codec_name', '-of', 'default=noprint_wrappers=1:nokey=1', path])\
        .decode().strip()


def get_audio_channels(path: str) -> str:
    """Retrieve the number of audio channels using FFMPEG"""

    channel_layout = subprocess.check_output(['ffprobe', '-show_entries', 'stream=channel_layout', '-select_streams',
                                              'a:0', '-of', 'compact=p=0:nk=1', '-v', '0', path]).decode().strip()
    layouts = {
        'mono': '1.0',
        'stereo': '2.0',
        '5.1(side)': '5.1'
    }

    if channel_layout not in layouts:
        raise ValueError(f"Unknown audio channel layout {channel_layout}")

    return layouts[channel_layout]


def get_audio_codec(path: str) -> str:
    """Retrieve the audio codec using FFMPEG"""

    return subprocess.check_output(['ffprobe', '-v', 'error', '-select_streams', 'a:0', '-show_entries',
                                    'stream=codec_name', '-of', 'default=noprint_wrappers=1:nokey=1', path])\
        .decode().strip().upper()


def dupe_exists(episode: Episode) -> bool:
    """Check if a duplicate copy (any height) of the episode exists in the destination"""

    if not os.path.isdir(episode.season.get_folder_name()):
        return False

    for file in os.listdir(episode.season.get_folder_name()):
        if re.search(episode.get_dupe_check_regex(), file):
            return True

    return False


def get_episode(episode: Episode) -> None:
    """Fetch an individual episode"""

    temp_path = f'{episode.season.get_folder_name()}{os.sep}temp.mp4'

    # delete existing temp/unfinished files in the destination folder
    if os.path.exists(temp_path):
        os.remove(temp_path)
    if os.path.exists(f'{temp_path}.part'):
        os.remove(f'{temp_path}.part')

    if dupe_exists(episode):
        print(f"Duplicate exists, skipping '{episode.get_filename()}'")
        return

    print(f'Starting {episode.get_filename()} ...')

    try:
        video_url = episode.get_video_url()
    except VideoError as e:
        print(f'Error: {e.error_msg}')
        return

    subprocess.run(["youtube-dl", f'-o{temp_path}', video_url])
    episode.populate_attributes_from_file(temp_path)

    final_path = f'{episode.season.get_folder_name()}{os.sep}{episode.get_filename()}'

    rename_file(temp_path, final_path)


def get_season(url) -> None:
    """Fetch a season"""

    try:
        curr_season = Season(url)
    except VideoError as e:
        print(e.error_msg)
        return

    for curr_episode in curr_season.episodes:
        get_episode(curr_episode)


def get_show(show: Show) -> None:
    """Fetch a show"""

    # Fetch regular seasons
    for curr_season_num in show.seasons:
        curr_url = f'https://watch.opb.org/show/{show.show_key}/episodes/season/{curr_season_num}/'
        get_season(curr_url)

    # Fetch specials, extras
    if show.has_specials:
        get_season(f'https://watch.opb.org/show/{show.show_key}/specials/')


@retry(PermissionError, delay=1, tries=5)
def rename_file(src: str, dest: str) -> None:
    """Rename a file, retry up to 5 times if the file is in use"""

    os.rename(src, dest)


def check_dependencies() -> None:
    """Check that all binary dependencies exist on the system, and validate Python's version"""

    if sys.version_info < (3, 8):
        raise ValueError('Required: Python 3.8 or later')

    binaries = ['youtube-dl', 'ffprobe']

    for binary in binaries:
        if not shutil.which(binary):
            raise ValueError(f'Could not find dependency: {binary}')


if __name__ == '__main__':

    check_dependencies()

    argparser = argparse.ArgumentParser()
    argparser.add_argument('show-key')
    argparser.add_argument('--group', help='Add a release group to file/folder names')

    args = argparser.parse_args()

    GROUP = args.group

    try:
        curr_show = Show(vars(args)['show-key'])
        get_show(curr_show)
    except ShowDoesNotExistError as err:
        print(err.error_msg)
