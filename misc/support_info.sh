#!/bin/bash

#set -x  # command tracing
set -o errexit
set -o nounset

PATH=/usr/local/bin:/usr/bin:/bin
export PATH


DISTRO_NAME=$(lsb_release -s -i)
DISTRO_RELEASE=$(lsb_release -s -r)
CPU_ARCH=$(uname -m)
CPU_BITS=$(getconf LONG_BIT)
CPU_TOTAL=$(grep -c "^proc" /proc/cpuinfo)
MEM_TOTAL=$(grep MemTotal /proc/meminfo | awk "{print \$2}")


if which indiserver >/dev/null 2>&1; then
    INDISERVER=$(which indiserver)
else
    INDISERVER="not found"
fi


SCRIPT_DIR=$(dirname "$0")
cd "$SCRIPT_DIR/.."
ALLSKY_DIRECTORY=$PWD
cd "$OLDPWD"


echo "#################################"
echo "### indi-allsky support info  ###"
echo "#################################"
echo
echo "Distribution: $DISTRO_NAME"
echo "Release: $DISTRO_RELEASE"
echo "Arch: $CPU_ARCH"
echo "Bits: $CPU_BITS"
echo
echo "CPUs: $CPU_TOTAL"
echo "Memory: $MEM_TOTAL kB"
echo
echo "Filesystems"
df -k
echo
echo "system python: $(python3 -V)"
echo
echo "indiserver: $INDISERVER"
echo


if [ -f "/etc/astroberry.version" ]; then
    echo "Detected Astroberry server"
    echo
fi


echo
echo "User info"
id
echo

echo "Process info"
# shellcheck disable=SC2009
ps auxwww | grep indi | grep -v grep
echo

echo "USB info"
lsusb
echo


echo "Module info"
lsmod
echo


if pkg-config --exists libindi; then
    DETECTED_INDIVERSION=$(pkg-config --modversion libindi)
    echo "indi version: $DETECTED_INDIVERSION"
    echo
else
    echo "indi version: not detected"
    echo
fi


echo "indi packages"
dpkg -l | grep libindi || true
echo


if pkg-config --exists libcamera; then
    DETECTED_LIBCAMERA=$(pkg-config --modversion libcamera)
    echo "libcamera version: $DETECTED_LIBCAMERA"
    echo
else
    echo "libcamera: not detected"
    echo
fi


echo "libcamera packages"
dpkg -l | grep libcamera || true
echo

echo "libcamera cameras"
if which libcamera-hello >/dev/null 2>&1; then
    echo "libcamera-hello: $(which libcamera-hello)"
    libcamera-hello --list-cameras || true
    echo
else
    echo "libcamera-hello not available"
    echo
fi


echo "python packages"
dpkg -l | grep python || true
echo


if [ -d "${ALLSKY_DIRECTORY}/virtualenv/indi-allsky" ]; then
    echo "Detected indi-allsky virtualenv"

    # shellcheck source=/dev/null
    source "${ALLSKY_DIRECTORY}/virtualenv/indi-allsky/bin/activate"
    echo "virtualenv python: $(python3 -V)"
    echo "virtualenv PATH: $PATH"
    echo "virtualenv python modules"
    pip freeze
    deactivate
    echo
else
    echo "indi-allsky virtualenv is not created"
    echo
fi

echo "#################################"
echo "###     end support info      ###"
echo "#################################"