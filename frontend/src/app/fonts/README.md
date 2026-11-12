# Bundled fonts

`montserrat-wght.woff2` is Montserrat (SIL Open Font License 1.1, see `OFL.txt`), from
`google/fonts` `ofl/montserrat/Montserrat[wght].ttf`, cut down with fontTools:

```bash
fonttools varLib.instancer 'Montserrat[wght].ttf' wght=400:700 -o m.ttf
pyftsubset m.ttf --flavor=woff2 --layout-features+=tnum,lnum \
  --unicodes="<Google Fonts' latin + latin-ext unicode-range>" \
  --output-file=montserrat-wght.woff2
```

It is in the repo so the build never fetches fonts: `next/font/google` downloads them at build
time and crashes the build when Google serves a URL without a file extension.
