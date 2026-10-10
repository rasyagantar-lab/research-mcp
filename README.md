# research-mcp

Server [MCP](https://modelcontextprotocol.io) lokal yang memberi Claude dua alat untuk web publik. Tanpa API key, tanpa akun, tanpa layanan hosting: server berjalan di komputer sendiri lewat stdio. Proyek independen, tidak berafiliasi dengan Anthropic.

| Alat | Fungsi |
|---|---|
| `search_web` | Meta-search ke beberapa mesin sekaligus (DuckDuckGo, Bing, Brave, dan lainnya, lewat [ddgs](https://pypi.org/project/ddgs/)). Hasilnya judul, URL, dan cuplikan; bisa difilter menurut kebaruan (`recency`) dan wilayah (`region`). |
| `read_webpage` | Membaca **satu** halaman web sebagai markdown, dengan tautan dan URL gambar tetap utuh. Halaman panjang dibaca bertahap lewat `offset` / `next_offset`. PDF yang punya lapisan teks juga bisa dibaca (teks ditandai `[page N]`). |

## Persyaratan

- Python 3.12 atau lebih baru, Git, dan satu klien MCP: Claude Desktop, Claude Code, atau VS Code.
- Diuji di Windows 11 dengan Python 3.14. macOS, Linux, dan Python 3.12/3.13 belum diuji.

## Pasang

```powershell
git clone https://github.com/rasyagantar-lab/research-mcp.git
cd research-mcp
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e .
```

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
| `RESEARCH_MCP_CONTACT` | Kontak (URL atau alamat email) yang ditambahkan ke header `User-Agent`. Kosong secara default dan tidak pernah diisi otomatis. Nilainya dikirim ke setiap situs yang dibaca, jadi isi hanya bila memang diperlukan. |
| `RESEARCH_MCP_LOG_DIR` | Folder log. Default: `logs/` di folder proyek. Satu file per proses, dihapus otomatis setelah 14 hari. |

## Keamanan

Isi halaman web adalah data yang tidak tepercaya. Server ini membantu dengan beberapa cara, tetapi tidak menghilangkan risikonya:

- Semua teks dari web dibungkus penanda `[UNTRUSTED WEB CONTENT]` agar model memperlakukannya sebagai data, bukan perintah. Prompt injection tetap mungkin terjadi.
- Alamat non-publik ditolak: loopback, privat, CGNAT, link-local (termasuk endpoint metadata cloud), multicast, dan reserved, juga bila disembunyikan di IPv6. Pemeriksaan dilakukan saat koneksi dibuat, jadi berlaku juga untuk redirect dan hasil DNS.
- Ukuran unduhan dibatasi menurut jenis konten, dan tiap pemanggilan alat punya batas waktu 45 detik.
- Log ditulis lokal dan hanya menyimpan host dan path URL, tanpa query.

## Batasan yang diketahui

- Tidak ada render JavaScript: halaman yang isinya baru muncul setelah skrip berjalan hanya terbaca sebagian. Parameter `render` pada `read_webpage` belum berfungsi.
- Tidak membaca gambar. PDF hasil scan (tanpa lapisan teks) atau yang berpassword ditolak.
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

Perintah pertama menjalankan tes offline; yang kedua hanya tes yang memakai internet sungguhan.

## Lisensi

MIT. Lihat [LICENSE](LICENSE).
