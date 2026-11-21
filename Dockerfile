# RaceRoom Racing Experience dedicated server on Linux: Wine + Microsoft .NET Framework 4.8.
#
# Two Wine DLLs are rebuilt from the Wine source with patches/ (see README, "Wine patch").
# WINE_BRANCH, WINE_VERSION and WINE_SOURCE_URL have to name the same Wine release.
ARG WINE_BRANCH=stable
ARG WINE_VERSION=11.0.0.0~noble-1
ARG WINE_SOURCE_URL=https://dl.winehq.org/wine/source/11.0/wine-11.0.tar.xz

# --- Stage 1: build the patched http.sys and httpapi.dll (64-bit only, takes a few minutes) ---
FROM ubuntu:24.04 AS winebuild
ARG WINE_SOURCE_URL
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates wget xz-utils patch make gcc libc6-dev \
      flex bison gcc-mingw-w64-x86-64 \
 && rm -rf /var/lib/apt/lists/*
RUN wget -q -O /wine.tar.xz "$WINE_SOURCE_URL" \
 && mkdir /wine && tar -xf /wine.tar.xz -C /wine --strip-components=1 && rm /wine.tar.xz
WORKDIR /wine
COPY patches/ /patches/
RUN for p in /patches/*.patch; do patch -p1 < "$p" || exit 1; done \
 && ./configure --enable-win64 --without-x --without-freetype --disable-tests >/dev/null \
 && make -j"$(nproc)" dlls/http.sys/x86_64-windows/http.sys dlls/httpapi/x86_64-windows/httpapi.dll >/dev/null \
 && mkdir /out && cp dlls/http.sys/x86_64-windows/http.sys dlls/httpapi/x86_64-windows/httpapi.dll /out/ \
 && x86_64-w64-mingw32-strip --strip-debug /out/*

# --- Stage 2: the server image ---
FROM ubuntu:24.04
ARG WINE_BRANCH
ARG WINE_VERSION
ENV DEBIAN_FRONTEND=noninteractive

RUN dpkg --add-architecture i386 \
 && apt-get update \
 && apt-get install -y --no-install-recommends software-properties-common ca-certificates wget curl xz-utils gnupg \
 && add-apt-repository -y multiverse \
 && apt-get update \
 && echo "steamcmd steam/question select I AGREE" | debconf-set-selections \
 && echo "steamcmd steam/license note ''" | debconf-set-selections \
 && apt-get install -y --no-install-recommends steamcmd xvfb xauth winbind cabextract procps unzip winetricks python3 \
 && rm -rf /var/lib/apt/lists/*

# WineHQ packages for Ubuntu 24.04 (noble)
RUN mkdir -pm755 /etc/apt/keyrings \
 && wget -qO /etc/apt/keyrings/winehq-archive.key https://dl.winehq.org/wine-builds/winehq.key \
 && wget -qNP /etc/apt/sources.list.d/ https://dl.winehq.org/wine-builds/ubuntu/dists/noble/winehq-noble.sources \
 && apt-get update \
 && V="${WINE_VERSION:+=${WINE_VERSION}}" \
 && apt-get install -y --install-recommends \
      "winehq-${WINE_BRANCH}${V}" "wine-${WINE_BRANCH}${V}" \
      "wine-${WINE_BRANCH}-amd64${V}" "wine-${WINE_BRANCH}-i386${V}" \
 && rm -rf /var/lib/apt/lists/*

# Replace Wine's HTTP server driver and its user-mode API with the patched builds.
COPY --from=winebuild /out/http.sys /out/httpapi.dll /opt/wine-${WINE_BRANCH}/lib/wine/x86_64-windows/

# The server front end calls xdg-open on start (to open a browser) and aborts if that fails.
RUN printf '#!/bin/sh\nexit 0\n' > /usr/local/bin/xdg-open && chmod +x /usr/local/bin/xdg-open

ENV PATH="$PATH:/usr/games"
COPY autostart.py /usr/local/bin/autostart.py
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# ubuntu:24.04 ships a user "ubuntu" with UID/GID 1000; ./data must be writable by it.
USER ubuntu
WORKDIR /home/ubuntu
ENTRYPOINT ["/entrypoint.sh"]
CMD ["run"]
