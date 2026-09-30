<a id="top"></a>
# PromptBase Profile Exporter

Export a public PromptBase profile's prompts into clean TXT, Markdown, JSON, CSV, or searchable HTML catalogs.

[![tests](https://github.com/IACBI/promptbase-profile-exporter/actions/workflows/tests.yml/badge.svg)](https://github.com/IACBI/promptbase-profile-exporter/actions/workflows/tests.yml)
[![release](https://img.shields.io/github/v/release/IACBI/promptbase-profile-exporter)](https://github.com/IACBI/promptbase-profile-exporter/releases/latest)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

**Read this in:** [English](#english) · [Türkçe](#turkce)

---

<a id="english"></a>
## English

### Overview

Give it a PromptBase profile URL or username, and it collects every approved
prompt on that profile with its title, description, and listing metadata, and
writes a tidy catalog. It is useful for backing up your own listings, auditing
them, or publishing a readable catalog somewhere else.

It reads the same public data the PromptBase website serves: no login, no API
key, no browser automation. It is written against the Python standard library
alone, so there is nothing to install beyond Python itself.

The same core ships three ways: a command-line tool, a local web UI, and a
GitHub Action for scheduled exports.

### Features

- Accepts a profile URL (with or without `https://`), a `profile/<name>` path, a
  username, or `@username`,
  and exports several profiles in one run.
- Exports a profile's prompts, bundles, or apps (`--item-type`).
- Writes `txt`, `markdown`, `json`, `csv`, or a self-contained `html` page you
  can search in the browser, split into `all`, `text`, and `image` catalogs or
  as a single file. `--csv-safe` keeps CSV cells from running as spreadsheet
  formulas. `--extra-fields` adds tags, engine, update and last-sale times,
  and unique sales on request.
- Filters by domain, prompt type, free/paid, price range, creation date,
  sales, rating, and count; sorts by date, title, price, views, sales,
  downloads, favorites, or rating.
- Compares a fresh export against a previous catalog, showing old and new
  values, as Markdown or JSON; rewrites it in place atomically; and can fail a
  CI job when the catalog drifts. `pb-diff` compares two saved catalogs
  offline, even across formats, and `pb-convert` rewrites a saved catalog in
  another format, matching a fresh export byte for byte.
- Checks every written file against the expected record count, and stops with
  a clear error if PromptBase changes its public data model instead of
  writing a misleading catalog.

### Requirements

- Python 3.10 or newer
- Network access to `firestore.googleapis.com`

### Installation

```bash
git clone https://github.com/IACBI/promptbase-profile-exporter.git
cd promptbase-profile-exporter
uv tool install .            # or: python -m pip install -e .
```

This puts four commands on your `PATH`: `pb` for exports, `pb-web` for the
web UI, `pb-diff` to compare two catalog files, and `pb-convert` to rewrite a
catalog in another format (long forms: `promptbase-export`,
`promptbase-export-web`, `promptbase-diff`, and `promptbase-convert`). Without
installing, run `python -m promptbase_exporter` from the project folder
instead of `pb`.

### Usage

```bash
pb https://promptbase.com/profile/acb     # all, text, and image catalogs as TXT in exports/
pb @acb --dry-run                         # show what would be written, write nothing
pb @acb --mode all --format json          # one JSON catalog with full metadata
pb @acb --type claude --paid-only --sort views --limit 25
pb @acb --mode all --update-file exports/acb_all_prompts.json   # refresh a catalog in place
pb @acb @dreamydesigns --mode all --format html                 # searchable HTML, two profiles
pb-diff old/acb_all_prompts.csv exports/acb_all_prompts.json    # compare two saved catalogs
pb-convert exports/acb_all_prompts.json --format csv            # rewrite a catalog, offline
```

The default run creates three files:

```text
exports/acb_all_prompts.txt
exports/acb_text_prompts.txt
exports/acb_image_prompts.txt
```

Prefer a browser? `pb-web` starts a local UI at <http://127.0.0.1:8765/> with
the export options as a form and a download link for each file.

To export on a schedule, use the GitHub Action:

```yaml
- uses: IACBI/promptbase-profile-exporter@v0.10.0
  with:
    profile-url: https://promptbase.com/profile/acb
    format: markdown
```

Further reading:

- [Command-line reference](docs/cli.md): every option, output formats,
  catalog comparison, exit codes
- [Web UI](docs/web-ui.md): running the local UI and its security model
- [GitHub Action](docs/github-action.md): inputs, scheduled exports,
  committing catalogs back to a repository
- [Changelog](CHANGELOG.md)

### Configuration

Everything is set per run with command-line options; there is no config file.
The ones you will reach for most:

| Option | Default | Purpose |
| --- | --- | --- |
| `--mode` | `split` | `split` (all + text + image), `all`, `text`, or `image` |
| `--format` | `txt` | `txt`, `markdown`, `json`, `csv`, or `html` |
| `--output-dir` | `exports` | Where generated files go |
| `--sort` | `newest` | `newest`, `oldest`, `title`, `price`, `views`, `sales`, `downloads`, `favorites`, `rating` |
| `--domain`, `--type` | none | Comma-separated filters, e.g. `--type gpt,claude` |
| `--since`, `--until` | none | Creation-date range, `YYYY-MM-DD` or ISO datetime (UTC) |
| `--min-sales`, `--min-rating` | none | Keep prompts with at least this many sales / this rating |

The command exits with `0` on success, `1` on any error, and `2` when
`--fail-on-diff` finds catalog changes. The
[command-line reference](docs/cli.md) documents every option.

### Contributing

Bug reports and pull requests are welcome. [CONTRIBUTING.md](CONTRIBUTING.md)
covers the development setup, the checks CI runs, and the pull-request
checklist. Please report security issues privately as described in
[SECURITY.md](SECURITY.md).

Only export profiles and data you are allowed to use, and respect PromptBase's
terms.

### License

[MIT](LICENSE) © 𝓐.𝓒.𝓑

[⬆ Back to top](#top)

---

<a id="turkce"></a>
## Türkçe

### Genel Bakış

Bir PromptBase profilinin adresini ya da kullanıcı adını verirsiniz; araç o
profildeki onaylı tüm prompt'ları başlık, açıklama ve ilan bilgileriyle
toplayıp düzenli bir katalog dosyasına yazar. Kendi ilanlarınızı yedeklemek,
gözden geçirmek ya da okunaklı bir katalog olarak başka bir yerde yayımlamak
için kullanışlıdır.

PromptBase sitesinin herkese açık olarak sunduğu veriyi okur; giriş yapmanız,
API anahtarı almanız ya da tarayıcı otomasyonu kurmanız gerekmez. Yalnızca
Python standart kütüphanesiyle yazıldığı için Python dışında bir şey kurmanız
da gerekmez.

Aynı çekirdek üç şekilde gelir: komut satırı aracı, yerel bir web arayüzü ve
zamanlanmış dışa aktarımlar için bir GitHub Action.

### Özellikler

- Profil adresi (`https://` ile ya da onsuz), `profile/<ad>` yolu, kullanıcı adı
  ya da `@kullanıcıadı` kabul eder; tek çalıştırmada birden fazla profili dışa aktarabilir.
- Bir profilin prompt'larını, bundle'larını ya da app'lerini dışa aktarır
  (`--item-type`).
- `txt`, `markdown`, `json`, `csv` ya da tarayıcıda arama yapılabilen, tek
  dosyalık bir `html` sayfası yazar; çıktıyı `all`, `text` ve `image`
  kataloglarına ayırabilir ya da tek dosya üretebilir. `--csv-safe`, CSV
  hücrelerinin tablo programlarında formül olarak çalışmasını engeller.
  `--extra-fields` istenirse etiketleri, motoru, güncelleme ve son satış
  zamanlarını ve tekil satışları da ekler.
- Alan (domain), prompt türü, ücretsiz/ücretli, fiyat aralığı, oluşturulma
  tarihi, satış, puan ve adet ile filtreler; tarih, başlık, fiyat,
  görüntülenme, satış, indirme, favori veya puana göre sıralar.
- Yeni dışa aktarımı önceki bir katalogla karşılaştırıp eski ve yeni değerleri
  Markdown ya da JSON olarak raporlar, dosyayı yerinde ve yarım kalma riski
  olmadan günceller, katalog değiştiğinde CI işini başarısız sayabilir.
  `pb-diff` ise kayıtlı iki kataloğu, biçimleri farklı olsa bile, internete
  bağlanmadan karşılaştırır; `pb-convert` kayıtlı bir kataloğu başka bir
  biçimde yeniden yazar ve sonuç taze bir dışa aktarımla bayt bayt aynıdır.
- Yazılan her dosyadaki kayıt sayısını doğrular. PromptBase herkese açık veri
  yapısını değiştirirse yanıltıcı bir katalog yazmak yerine açık bir hatayla
  durur.

### Gereksinimler

- Python 3.10 veya üzeri
- `firestore.googleapis.com` adresine ağ erişimi

### Kurulum

```bash
git clone https://github.com/IACBI/promptbase-profile-exporter.git
cd promptbase-profile-exporter
uv tool install .            # or: python -m pip install -e .
```

Bu, `PATH`'inize dört komut ekler: dışa aktarım için `pb`, web arayüzü için
`pb-web`, iki katalog dosyasını karşılaştırmak için `pb-diff`, bir kataloğu
başka bir biçimde yeniden yazmak için `pb-convert` (uzun adları:
`promptbase-export`, `promptbase-export-web`, `promptbase-diff` ve
`promptbase-convert`).
Kurulum yapmadan kullanmak isterseniz proje klasöründe `pb` yerine
`python -m promptbase_exporter` çalıştırın.

### Kullanım

```bash
pb https://promptbase.com/profile/acb     # all, text, and image catalogs as TXT in exports/
pb @acb --dry-run                         # show what would be written, write nothing
pb @acb --mode all --format json          # one JSON catalog with full metadata
pb @acb --type claude --paid-only --sort views --limit 25
pb @acb --mode all --update-file exports/acb_all_prompts.json   # refresh a catalog in place
pb @acb @dreamydesigns --mode all --format html                 # searchable HTML, two profiles
pb-diff old/acb_all_prompts.csv exports/acb_all_prompts.json    # compare two saved catalogs
pb-convert exports/acb_all_prompts.json --format csv            # rewrite a catalog, offline
```

Varsayılan çalıştırma üç dosya oluşturur:

```text
exports/acb_all_prompts.txt
exports/acb_text_prompts.txt
exports/acb_image_prompts.txt
```

Tarayıcıyı mı tercih edersiniz? `pb-web`, <http://127.0.0.1:8765/> adresinde
dışa aktarım seçeneklerini bir form olarak sunan ve her dosya için indirme
bağlantısı veren yerel bir arayüz başlatır.

Dışa aktarımı belirli aralıklarla çalıştırmak için GitHub Action'ı
kullanabilirsiniz:

```yaml
- uses: IACBI/promptbase-profile-exporter@v0.10.0
  with:
    profile-url: https://promptbase.com/profile/acb
    format: markdown
```

Ayrıntılı dokümanlar (İngilizce):

- [Komut satırı referansı](docs/cli.md): tüm seçenekler, çıktı biçimleri,
  katalog karşılaştırma, çıkış kodları
- [Web arayüzü](docs/web-ui.md): yerel arayüzü çalıştırma ve güvenlik modeli
- [GitHub Action](docs/github-action.md): girdiler, zamanlanmış dışa
  aktarımlar, katalogları repoya geri commit etme
- [Değişiklik günlüğü](CHANGELOG.md)

### Yapılandırma

Her şey çalıştırma sırasında komut satırı seçenekleriyle ayarlanır; ayrı bir
yapılandırma dosyası yoktur. En sık kullanacaklarınız:

| Seçenek | Varsayılan | Amaç |
| --- | --- | --- |
| `--mode` | `split` | `split` (all + text + image), `all`, `text` veya `image` |
| `--format` | `txt` | `txt`, `markdown`, `json`, `csv` veya `html` |
| `--output-dir` | `exports` | Üretilen dosyaların yazılacağı klasör |
| `--sort` | `newest` | `newest`, `oldest`, `title`, `price`, `views`, `sales`, `downloads`, `favorites`, `rating` |
| `--domain`, `--type` | yok | Virgülle ayrılmış filtreler, ör. `--type gpt,claude` |
| `--since`, `--until` | yok | Oluşturulma tarihi aralığı, `YYYY-MM-DD` ya da ISO tarih-saat (UTC) |
| `--min-sales`, `--min-rating` | yok | En az bu kadar satışı / bu puanı olan prompt'ları tutar |

Komut başarılı olduğunda `0`, herhangi bir hatada `1`, `--fail-on-diff`
katalogda değişiklik bulduğunda ise `2` koduyla çıkar. Tüm seçenekler
[komut satırı referansında](docs/cli.md) anlatılıyor.

### Katkı

Hata bildirimleri ve pull request'ler memnuniyetle karşılanır. Geliştirme
ortamı, CI'ın çalıştırdığı kontroller ve pull request kontrol listesi
[CONTRIBUTING.md](CONTRIBUTING.md) dosyasında. Güvenlik açıklarını lütfen
[SECURITY.md](SECURITY.md) dosyasında anlatıldığı gibi gizli olarak bildirin.

Yalnızca kullanma hakkınız olan profilleri ve verileri dışa aktarın ve
PromptBase'in kullanım koşullarına uyun.

### Lisans

[MIT](LICENSE) © 𝓐.𝓒.𝓑

[⬆ Başa Dön](#top)
