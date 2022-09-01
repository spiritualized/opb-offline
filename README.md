# opb-offline

Save shows from the Oregon Public Broadcasting site for offline viewing.

### Installation
Python 3.8 or later is required, and youtube-dl must be available on the system path. Requirements can be added to your Python installation by running `pip3 install -r requirements.txt`.

### Usage
Browse the Oregon Public Broadcasting site and find a show you want to view offline. Extract the show's URL key. For example, https://watch.opb.org/show/oregon-art-beat/ has a URL key `oregon-art-beat`. 

Pass this as a parameter to the script: `python3 opb-offline.py oregon-art-beat`.

All seasons of the show will be downloaded to container folders in the current working directory.

## License

Released under [GNU GPLv3](http://www.gnu.org/licenses/gpl-3.0.en.html)