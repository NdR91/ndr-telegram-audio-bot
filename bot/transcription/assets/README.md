# Included transcription sample

`transcription-sample.wav` is a locally synthesized English sample. It contains
“The blue bird sings at sunrise.” The packaged file is mono, 16 kHz, signed
16-bit PCM, and 1.858875 seconds long. Its SHA-256 is
`04ab9192ded8a373a60ecc8f3c3da956694e0360630ce66487d7596dfe63d922`.

Provenance: generated on 2026-10-29 with FFmpeg 5.1.9 and its `flite` audio
source filter, using the `kal` voice from CMU Flite 2.2. The phrase and audio
were synthesized locally; no personal recording or remote service was used.
The generation command was:

```sh
ffmpeg -f lavfi -i "flite=text='The blue bird sings at sunrise.':voice=kal" \
  -t 4 -ac 1 -ar 16000 -c:a pcm_s16le -y transcription-sample.wav
```

The Flite engine and voice data are copyright Carnegie Mellon University and
contributors, © 1999–2001, and are distributed under the BSD-style license
stated in Debian's `libflite1` 2.2-5 copyright notice. The applicable notice
reads:

> Permission is hereby granted, free of charge, to use and distribute this
> software and its documentation without restriction, including without
> limitation the rights to use, copy, modify, merge, publish, distribute,
> sublicense, and/or sell copies of this work, and to permit persons to whom
> this work is furnished to do so, subject to the following conditions:
>
> 1. The code must retain the above copyright notice, this list of conditions
>    and the following disclaimer.
> 2. Any modifications must be clearly marked as such.
> 3. Original authors' names are not deleted.
> 4. The authors' names are not used to endorse or promote products derived
>    from this work without specific prior written permission.
>
> CARNEGIE MELLON UNIVERSITY AND THE CONTRIBUTORS TO THIS WORK DISCLAIM ALL
> WARRANTIES WITH REGARD TO THIS SOFTWARE, INCLUDING ALL IMPLIED WARRANTIES
> OF MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL CARNEGIE MELLON UNIVERSITY
> NOR THE CONTRIBUTORS BE LIABLE FOR ANY SPECIAL, INDIRECT OR CONSEQUENTIAL
> DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR
> PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS
> ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS
> SOFTWARE.

The notice is transcribed from the Debian source package's
`debian/copyright`. This synthesized recording is included as a small test
fixture with that attribution.

At runtime the application copies the packaged sample into its temporary
working directory, sends only that copy through the same conversion and
transcription runner used for uploads, and removes it on success, failure, or
cancellation. The expected phrase is displayed to the operator but is never
sent to the provider or stored in SQLite.
