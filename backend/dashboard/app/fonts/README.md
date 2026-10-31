# Dashboard fonts

Latin-only, normal-style WOFF2 files copied unchanged from these npm packages:

| Package                         | Version | Files                                                  |
| ------------------------------- | ------- | ------------------------------------------------------ |
| `@fontsource-variable/inter`    | 5.3.0   | `files/inter-latin-wght-normal.woff2`                  |
| `@fontsource-variable/fraunces` | 5.3.0   | `files/fraunces-latin-wght-normal.woff2`               |
| `@fontsource/ibm-plex-mono`     | 5.3.0   | `files/ibm-plex-mono-latin-{400,500,600}-normal.woff2` |

The five font files total 130,092 bytes. Inter and Fraunces vary only the weight
axis; their other axes remain at the upstream defaults. The dashboard uses Inter
400–700, IBM Plex Mono 400/500/600 and Fraunces 500 through `next/font/local`, so
neither builds nor browsers need to download fonts from Google.

Each family's OFL 1.1 license and copyright notice is included in its
`*-LICENSE.txt` file. Keep those notices with the font files when redistributing.
These packages are source archives only, not application dependencies.
