# Installation

## Arch Linux
sudo pacman -S mpv yt-dlp cava jp2a python

## Ubuntu / Tuxedo OS / Debian
sudo apt update
sudo apt install mpv cava jp2a python3 python3-pip
sudo install -m 755 <(curl -L https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp) /usr/local/bin/yt-dlp

## Termux (Android)
pkg update
pkg install mpv python git curl
pip install yt-dlp

# Optional: build cava on Termux
pkg install fftw ncurses clang make autoconf automake libtool
git clone https://github.com/karlstav/cava
cd cava
./autogen.sh
./configure
make
make install
