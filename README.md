# research-mcp

Server [MCP](https://modelcontextprotocol.io) lokal yang memberi Claude alat untuk web publik. Tanpa API key, tanpa akun, tanpa layanan hosting: server berjalan di komputer sendiri lewat stdio. Proyek independen, tidak berafiliasi dengan Anthropic.

| Alat | Fungsi |
|---|---|
| `search_web` | Meta-search ke beberapa mesin sekaligus (DuckDuckGo, Bing, Brave, dan lainnya, lewat [ddgs](https://pypi.org/project/ddgs/)). Hasilnya judul, URL, dan cuplikan; bisa difilter menurut kebaruan (`recency`) dan wilayah (`region`). |
| `read_webpage` | Membaca **satu** halaman web sebagai markdown, dengan tautan dan URL gambar tetap utuh. Halaman panjang dibaca bertahap lewat `offset` / `next_offset`. PDF yang punya lapisan teks juga bisa dibaca (teks ditandai `[page N]`). |
| `download_file` | Mengunduh **satu** file (gambar, PDF, arsip, data, dll., maks. 50 MB) ke folder riset lokal. Hasilnya path, ukuran, jenis konten, dan SHA-256, jadi file bisa langsung diunggah ke artifact atau doc lalu dicek keutuhannya. Nama file diambil dari parameter `filename`, header server, atau URL; `folder` memilih subfolder. File yang sudah ada tidak pernah ditimpa (nama baru jadi `nama (1).ext`). |

Alat diagnostik (bisa dimatikan, lihat [Konfigurasi opsional](#konfigurasi-opsional)):

| Alat | Fungsi |
|---|---|
| `spike_image` | Mengembalikan PNG berisi kode acak (mis. `SALAK-7878`) yang diganjal sampai `target_kb`. Kalau Claude bisa membacakan kodenya, gambar sebesar itu lolos. Kodenya hanya ada di gambar dan di log lokal. |
| `spike_image_structured` | Sama, tetapi fakta gambar (ukuran, SHA-256) juga dikirim sebagai structured content. |
| `spike_sleep` | Menunggu `seconds` detik (maks. 900) sambil mengirim notifikasi progress tiap 5 detik bila klien memintanya, untuk mengukur batas waktu panggilan alat. |

Hasil pengukuran lewat Claude (Okt 2026): gambar 200 KB dan 600 KB lolos, 900 KB (sekitar 1200 KB setelah base64) ditolak; `spike_sleep` 30, 50, dan 70 detik lolos.

## Persyaratan

- Python 3.12 atau lebih baru, Git, dan satu klien MCP: Claude Desktop, Claude Code, atau VS Code.
- Diuji di Windows 11 dengan Python 3.13 dan 3.14. macOS, Linux, dan Python 3.12 belum diuji.

## Pasang

```powershell
git clone https://github.com/rasyagantar-lab/research-mcp.git
cd research-mcp
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install -e .
```

`py -3.13` memilih versi Python lewat Python Launcher; versi Python yang sangat baru kadang belum punya wheel untuk semua dependensi (lxml, pydantic-core, primp), jadi pakai versi yang sudah matang.

Pasang dengan `-e` (editable) dan jangan memindahkan folder ini sesudahnya: venv mencatat lokasinya. Di macOS/Linux, ganti `.venv\Scripts\python.exe` dengan `.venv/bin/python`.

## Sambungkan ke klien

Ganti `C:\path\ke\research-mcp` dengan lokasi folder hasil clone. Path harus absolut, dan di dalam JSON setiap `\` ditulis `\\`. Yang sudah diuji penulis adalah Claude Desktop di Windows; sintaks Claude Code dan VS Code mengikuti dokumentasi resmi masing-masing.

### Claude Desktop

Buka menu Claude, pilih **Settings…**, lalu tab **Developer**, lalu **Edit Config**. Tambahkan server ke `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "research": {
      "command": "C:\\path\\ke\\research-mcp\\.venv\\Scripts\\python.exe",
      "args": ["-m", "research_mcp"]
    }
  }
}
```

Tutup Claude Desktop sepenuhnya (bukan hanya jendelanya), lalu buka lagi. Server muncul di menu **Add files, connectors, and more** pada kolom pesan. Pada instalasi MSIX (Microsoft Store), file config ada di `%LOCALAPPDATA%\Packages\Claude_…\LocalCache\Roaming\Claude\`; tombol Edit Config membukanya. Jika server tidak muncul, lihat log di `%APPDATA%\Claude\logs` (stderr server ada di `mcp-server-research.log`).

### Claude Code

```powershell
claude mcp add --scope user --transport stdio research -- "C:\path\ke\research-mcp\.venv\Scripts\python.exe" -m research_mcp
```

`--scope user` membuat server tersedia di semua proyek.

### VS Code (GitHub Copilot, mode agent)

Buat `.vscode/mcp.json` di folder proyek, atau jalankan **MCP: Open User Configuration** dari Command Palette agar berlaku di semua proyek:

```json
{
  "servers": {
    "research": {
      "type": "stdio",
      "command": "C:\\path\\ke\\research-mcp\\.venv\\Scripts\\python.exe",
      "args": ["-m", "research_mcp"]
    }
  }
}
```

Jalankan **MCP: List Servers** untuk menyalakan dan memeriksa server.

Start pertama bisa memakan beberapa detik (sebagian besar untuk memuat SDK MCP), dan lebih lama tepat setelah komputer menyala.

## Konfigurasi opsional

Diatur lewat variabel lingkungan. Pada Claude Desktop, taruh di blok `"env"` pada config di atas.

| Variabel | Fungsi |
|---|---|
| `RESEARCH_MCP_CONTACT` | Kontak pribadi (URL atau alamat email) yang ditambahkan ke header `User-Agent`. Kosong secara default dan tidak pernah diisi otomatis. Nilainya dikirim ke setiap situs yang dibaca, jadi isi hanya bila memang diperlukan. |
| `RESEARCH_MCP_LOG_DIR` | Folder log. Default: `logs/` di folder proyek. Satu file per proses, dihapus otomatis setelah 14 hari. |
| `RESEARCH_MCP_DOWNLOAD_DIR` | Folder riset tujuan `download_file` (path absolut). Default: `research-files` di folder user, misalnya `C:\Users\<nama>\research-files`. Bagikan folder ini ke Claude (Cowork) agar file hasil unduhan bisa langsung dipakai di artifact atau doc. |
| `RESEARCH_MCP_DIAGNOSTICS` | `on` (default) atau `off`. `off` menyembunyikan alat `spike_*`. |

## Keamanan

Isi halaman web adalah data yang tidak tepercaya. Server ini membantu dengan beberapa cara, tetapi tidak menghilangkan risikonya:

- Semua teks dari web dibungkus penanda `[UNTRUSTED WEB CONTENT]` agar model memperlakukannya sebagai data, bukan perintah. Prompt injection tetap mungkin terjadi.
- Alamat non-publik ditolak: loopback, privat, CGNAT, link-local (termasuk endpoint metadata cloud), multicast, dan reserved, juga bila disembunyikan di IPv6. Pemeriksaan dilakukan saat koneksi dibuat, jadi berlaku juga untuk redirect dan hasil DNS.
- Ukuran unduhan dibatasi menurut jenis konten, dan tiap pemanggilan alat punya batas waktu 45 detik (kecuali `spike_sleep`, yang memang mengukur batas itu).
- `download_file` hanya menulis di dalam folder riset: path absolut, `..`, dan junction yang keluar dari folder ditolak; nama file dibersihkan dari karakter dan nama perangkat yang dilarang Windows.
- Header `User-Agent` menyebut nama dan URL proyek ini (Wikimedia menolak bot tanpa URL), tanpa data pribadi.
- Log ditulis lokal dan hanya menyimpan host dan path URL, tanpa query.

## Batasan yang diketahui

- Tidak ada render JavaScript: halaman yang isinya baru muncul setelah skrip berjalan hanya terbaca sebagian. Parameter `render` pada `read_webpage` belum berfungsi.
- `read_webpage` tidak membaca gambar (pakai browser bawaan Claude, atau `download_file` untuk menyimpannya). PDF hasil scan (tanpa lapisan teks) atau yang berpassword ditolak.
- Gambar yang dikirim lewat alat MCP dibatasi klien sekitar 1200 KB setelah base64 (lihat hasil `spike_image` di atas).
- Situs yang memblokir bot (cek bot, CAPTCHA, 403) tidak ditembus; itu disengaja. Khusus situs MediaWiki yang kena cek bot (misalnya sebagian halaman Fandom), isinya dibaca lewat `api.php`.
- Hasil `search_web` bergantung pada mesin yang terjangkau dari jaringan yang dipakai. Pada sebagian ISP, DNS dicegat sehingga DuckDuckGo tak terjangkau; server lalu memakai mesin lain dan melaporkan bahwa hasilnya tidak lengkap, bukan berpura-pura kosong.
- Jika situs publik ditolak dengan `blocked_private_address`, kemungkinan DNS, VPN, atau proxy mengarahkan nama itu ke alamat privat. Alamat yang dilihat server tercatat di log lokal.
- Belum diaudit keamanannya oleh pihak independen. Pakai dengan risiko sendiri (lihat lisensi).

## Tes

```powershell
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m pytest -m live
```

Perintah kedua menjalankan tes offline; yang ketiga hanya tes yang memakai internet sungguhan.

## Riwayat versi

| Versi | Perubahan |
|---|---|
| 0.2.0 | `download_file`; alat diagnostik `spike_image`, `spike_image_structured`, `spike_sleep`; `User-Agent` menyertakan URL proyek; variabel `RESEARCH_MCP_DOWNLOAD_DIR` dan `RESEARCH_MCP_DIAGNOSTICS`. |
| 0.1.0 | Rilis awal: `search_web` dan `read_webpage`. |

## Lisensi

MIT. Lihat [LICENSE](LICENSE).
