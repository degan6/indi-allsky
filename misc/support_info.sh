#!/bin/bash

#set -x  # command tracing
set -o errexit
set -o nounset

PATH=/usr/local/bin:/usr/bin:/bin
export PATH


function catch_error() {
    echo
    echo
    echo "\`\`\`"  # markdown
    echo "###############"
    echo "###  ERROR  ###"
    echo "###############"
    echo
    echo "The script exited abnormally, please try to run again..."
    echo
    echo
    exit 1
}
trap catch_error ERR

function catch_sigint() {
    echo
    echo
    echo "\`\`\`"  # markdown
    echo "###############"
    echo "###  ERROR  ###"
    echo "###############"
    echo
    echo "The script was interrupted, please run the script again to finish..."
    echo
    echo
    exit 1
}
trap catch_sigint SIGINT


if [ ! -f "/etc/os-release" ]; then
    echo
    echo "Unable to determine OS from /etc/os-release"
    echo
    exit 1
fi

source /etc/os-release


DISTRO_ID="${ID:-unknown}"
DISTRO_VERSION_ID="${VERSION_ID:-unknown}"
CPU_ARCH=$(uname -m)
CPU_BITS=$(getconf LONG_BIT)
CPU_TOTAL=$(grep -c "^proc" /proc/cpuinfo)
MEM_TOTAL=$(grep MemTotal /proc/meminfo | awk "{print \$2}")


if [ -f "/proc/device-tree/model" ]; then
    SYSTEM_MODEL=$(cat /proc/device-tree/model)
else
    SYSTEM_MODEL="Generic PC"
fi


if which indiserver >/dev/null 2>&1; then
    INDISERVER=$(which indiserver)
else
    INDISERVER="not found"
fi


SCRIPT_DIR=$(dirname "$0")
cd "$SCRIPT_DIR/.."
ALLSKY_DIRECTORY=$PWD
cd "$OLDPWD"


# go ahead and prompt for password
#sudo true


echo "#################################"
echo "### indi-allsky support info  ###"
echo "#################################"

sleep 3

echo "\`\`\`"  # markdown
echo
echo "Distribution: $DISTRO_ID"
echo "Release: $DISTRO_VERSION_ID"
echo "Arch: $CPU_ARCH"
echo "Bits: $CPU_BITS"
echo
echo "CPUs: $CPU_TOTAL"
echo "Memory: $MEM_TOTAL kB"
echo
echo "System: $SYSTEM_MODEL"

echo
uname -a

echo
echo "Time"
date

echo
echo "System timezone"
cat /etc/timezone || true

echo
echo "Uptime"
uptime

echo
echo "Memory"
free

echo
echo "Filesystems"
df -k

echo
echo "sysctl info"
/usr/sbin/sysctl vm.swappiness

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
ps auxwww | grep indi | grep -v grep || true
echo

echo "USB info"
lsusb
echo

echo "USB Permissions"
find /dev/bus/usb -ls || true
echo

echo "video device Permissions"
ls -l /dev/video* || true
echo

echo "v4l info"
v4l2-ctl --list-devices || true
echo

echo "Module info"
lsmod
echo


echo "git status"
git status | head -n 100
echo


echo "git log"
git log -n 1 | head -n 100
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


echo "indi connections"
ss -ant | grep 7624 || true
echo


if which indi_getprop >/dev/null 2>&1; then
    echo "Detected indi properties"
    indi_getprop -v 2>&1 || true
    echo
fi


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
if which rpicam-hello >/dev/null 2>&1; then
    echo "rpicam-hello: $(which rpicam-hello)"
    rpicam-hello --list-cameras || true
    echo
elif which libcamera-hello >/dev/null 2>&1; then
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

    if which flask >/dev/null 2>&1; then
        echo "flask command: $(which flask)"
    else
        echo "flask: not found"
    fi

    echo
    echo "virtualenv python modules"
    pip freeze

    echo "\`\`\`"  # markdown

    echo
    echo "indi-allsky config (passwords redacted)"
    INDI_ALLSKY_CONFIG=$("${ALLSKY_DIRECTORY}/config.py" dump)

    echo "\`\`\`json"  # markdown
    # Remove all secrets from config
    echo "$INDI_ALLSKY_CONFIG" | jq --arg redacted "REDACTED" '.FILETRANSFER.PASSWORD = $redacted | .FILETRANSFER.PASSWORD_E = $redacted | .S3UPLOAD.SECRET_KEY = $redacted | .S3UPLOAD.SECRET_KEY_E = $redacted | .MQTTPUBLISH.PASSWORD = $redacted | .MQTTPUBLISH.PASSWORD_E = $redacted | .SYNCAPI.APIKEY = $redacted | .SYNCAPI.APIKEY_E = $redacted | .PYCURL_CAMERA.PASSWORD = $redacted | .PYCURL_CAMERA.PASSWORD_E = $redacted'

    deactivate
    echo
else
    echo "indi-allsky virtualenv is not created"
    echo
fi

echo "\`\`\`"  # markdown
echo "#################################"
echo "###     end support info      ###"
echo "#################################"
