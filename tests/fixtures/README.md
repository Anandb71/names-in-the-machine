# Fixtures

Every line under `doc/` was copied with `sed -n` from `/usr/share/doc/<package>/copyright`
on the machine this was built on (Debian 13). Line numbers differ from the originals because
each file is an excerpt:

| file | copied lines |
|---|---|
| `dpkg/copyright` | 1-9, then a `License: GPL-2+` line added to close the field |
| `base-files/copyright` | 1-8 |
| `zlib1g/copyright` | 1-14 |
| `diffutils/copyright` | 1-13 |
| `libpython3.13-minimal/copyright` | 503-511 (the xmlrpclib notice) |
| `ffmpeg/copyright` | 1, a blank line, 857-866 |
| `openjdk-21-jre-headless/copyright` | 1, a blank line, 67, 360-386 (the GPL "How to Apply" text) |
| `acl/copyright` | 1-9 (a holder list that a naive regex would join to `License`) |

`pip/html2text-2025.4.15.dist-info/METADATA`: lines 1-5 are real. The `Maintainer-email`
line (Jane Placeholder, `example.org`) is invented, so the living-people tests have a maintainer to keep off
the page without naming a real one.
