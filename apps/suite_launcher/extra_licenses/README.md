# Extra licence texts

Packages whose wheel ships no licence file, although their licence requires the notice to be reproduced in a binary redistribution (the
portable bundle). `collect_third_party_licenses.py` copies `<package name in lower case>.txt` from here into `THIRD_PARTY_LICENSES` for a
package for which it found no file of its own. Add the text from the project's repository, with the URL it came from in this list:

- `pyserial.txt` - https://github.com/pyserial/pyserial/blob/master/LICENSE.txt (BSD-3-Clause, Chris Liechti)
