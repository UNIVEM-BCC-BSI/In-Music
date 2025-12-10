import re
import time
import sqlite3
import os
import requests
from lxml import html
from flask import Flask, request, render_template_string, redirect
import html as html_lib

DB_PATH = "inmusic.db"
LOG_PATH = "crawler_log.txt"

G1_URL = "https://g1.globo.com/pop-arte/musica/"
POPLINE_URL = "https://portalpopline.com.br/categoria/musica/"
TRACKLIST_URL = "https://tracklist.com.br/categoria/noticias/"

UA_HEADER = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def log_error(contexto, erro):
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            ts = time.strftime("%d/%m/%Y %H:%M:%S", time.localtime())
            f.write(f"[{ts}] {contexto}: {erro}\n")
    except Exception:
        pass


def db_connect():
    return sqlite3.connect(DB_PATH)


def init_db():
    con = db_connect()
    cur = con.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS news (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            titulo TEXT,
            imagem_url TEXT,
            resumo TEXT,
            texto_completo TEXT,
            link TEXT UNIQUE,
            autor TEXT,
            site TEXT,
            categoria TEXT,
            views INTEGER DEFAULT 0,
            created_at INTEGER,
            likes INTEGER DEFAULT 0,
            liked INTEGER DEFAULT 0
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            news_id INTEGER,
            nome TEXT,
            texto TEXT,
            created_at INTEGER
        )
        """
    )
    con.commit()
    con.close()


def classify_category(titulo, resumo):
    texto = f"{titulo} {resumo}".lower()
    if any(p in texto for p in ["show", "turnê", "turne", "apresentação", "festival"]):
        return "Shows"
    if any(p in texto for p in ["álbum", "album", "disco", "single", "faixa", "lançamento"]):
        return "Lançamentos"
    if any(p in texto for p in ["lista", "top", "ranking", "os melhores"]):
        return "Listas"
    return "Outros"


def save_news_batch(news_list):
    con = db_connect()
    cur = con.cursor()
    now = int(time.time())
    for n in news_list:
        try:
            titulo = n["titulo"]
            resumo = n.get("resumo") or ""
            categoria = classify_category(titulo, resumo)
            cur.execute(
                """
                INSERT OR IGNORE INTO news
                (titulo, imagem_url, resumo, texto_completo, link,
                 autor, site, categoria, views, created_at, likes, liked)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    titulo,
                    n.get("imagem_url"),
                    resumo,
                    n.get("texto_completo"),
                    n.get("link"),
                    n.get("autor"),
                    n.get("site"),
                    categoria,
                    0,
                    now,
                    0,
                    0,
                ),
            )
        except Exception as e:
            print("DB erro ao salvar notícia:", e)
            log_error("save_news_batch", e)
    con.commit()
    con.close()


def load_news(limit=200, offset=0):
    con = db_connect()
    cur = con.cursor()
    cur.execute(
        """
        SELECT id, titulo, imagem_url, resumo, texto_completo, link,
               autor, site, categoria, views, created_at, likes, liked
        FROM news
        ORDER BY created_at DESC, id DESC
        LIMIT ? OFFSET ?
        """,
        (limit, offset),
    )
    rows = cur.fetchall()
    con.close()
    out = []
    for r in rows:
        ts = r[10] or int(time.time())
        data_fmt = time.strftime("%d/%m/%Y %H:%M", time.localtime(ts))
        out.append(
            {
                "id": r[0],
                "titulo": r[1],
                "imagem_url": r[2],
                "resumo": r[3],
                "texto_completo": r[4],
                "link": r[5],
                "autor": r[6],
                "site": r[7],
                "categoria": r[8],
                "views": r[9],
                "data": data_fmt,
                "likes": r[11],
                "liked": r[12],
            }
        )
    return out


def load_liked(limit=200):
    con = db_connect()
    cur = con.cursor()
    cur.execute(
        """
        SELECT id, titulo, imagem_url, resumo, texto_completo, link,
               autor, site, categoria, views, created_at, likes, liked
        FROM news
        WHERE liked = 1
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        (limit,),
    )
    rows = cur.fetchall()
    con.close()
    out = []
    for r in rows:
        ts = r[10] or int(time.time())
        data_fmt = time.strftime("%d/%m/%Y %H:%M", time.localtime(ts))
        out.append(
            {
                "id": r[0],
                "titulo": r[1],
                "imagem_url": r[2],
                "resumo": r[3],
                "texto_completo": r[4],
                "link": r[5],
                "autor": r[6],
                "site": r[7],
                "categoria": r[8],
                "views": r[9],
                "data": data_fmt,
                "likes": r[11],
                "liked": r[12],
            }
        )
    return out


def count_news():
    con = db_connect()
    cur = con.cursor()
    cur.execute("SELECT COUNT(*) FROM news")
    total = cur.fetchone()[0]
    con.close()
    return total


def load_one(id_):
    con = db_connect()
    cur = con.cursor()
    cur.execute(
        """
        SELECT titulo, imagem_url, texto_completo, autor, site,
               categoria, views, created_at, likes, liked
        FROM news WHERE id=?
        """,
        (id_,),
    )
    row = cur.fetchone()
    con.close()
    if not row:
        return None
    ts = row[7] or int(time.time())
    data_fmt = time.strftime("%d/%m/%Y %H:%M", time.localtime(ts))
    return {
        "titulo": row[0],
        "imagem_url": row[1],
        "texto_completo": row[2],
        "autor": row[3],
        "site": row[4],
        "categoria": row[5],
        "views": row[6],
        "data": data_fmt,
        "likes": row[8],
        "liked": row[9],
    }


def increment_views(id_):
    try:
        con = db_connect()
        cur = con.cursor()
        cur.execute("UPDATE news SET views = views + 1 WHERE id = ?", (id_,))
        con.commit()
        con.close()
    except Exception as e:
        print("Erro ao atualizar views:", e)
        log_error("increment_views", e)


def toggle_like(id_):
    try:
        con = db_connect()
        cur = con.cursor()
        cur.execute("SELECT likes, liked FROM news WHERE id = ?", (id_,))
        row = cur.fetchone()
        if not row:
            con.close()
            return
        likes, liked = row
        if liked:
            likes = max(0, likes - 1)
            liked = 0
        else:
            likes = likes + 1
            liked = 1
        cur.execute("UPDATE news SET likes=?, liked=? WHERE id=?", (likes, liked, id_))
        con.commit()
        con.close()
    except Exception as e:
        print("Erro ao atualizar curtida:", e)
        log_error("toggle_like", e)


def load_most_viewed(limit=5):
    con = db_connect()
    cur = con.cursor()
    cur.execute(
        """
        SELECT id, titulo, views
        FROM news
        ORDER BY views DESC, created_at DESC
        LIMIT ?
        """,
        (limit,),
    )
    rows = cur.fetchall()
    con.close()
    return [{"id": r[0], "titulo": r[1], "views": r[2]} for r in rows]


def add_comment(news_id, nome, texto):
    if not texto.strip():
        return
    if not nome.strip():
        nome = "Anônimo"
    con = db_connect()
    cur = con.cursor()
    cur.execute(
        """
        INSERT INTO comments (news_id, nome, texto, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (news_id, nome.strip(), texto.strip(), int(time.time())),
    )
    con.commit()
    con.close()


def load_comments(news_id):
    con = db_connect()
    cur = con.cursor()
    cur.execute(
        """
        SELECT nome, texto, created_at
        FROM comments
        WHERE news_id = ?
        ORDER BY created_at ASC
        """,
        (news_id,),
    )
    rows = cur.fetchall()
    con.close()
    out = []
    for r in rows:
        ts = r[2]
        data_fmt = time.strftime("%d/%m/%Y %H:%M", time.localtime(ts))
        out.append({"nome": r[0], "texto": r[1], "data": data_fmt})
    return out


def search_news(term, limit=200, order="recentes"):
    term_like = f"%{term}%"
    con = db_connect()
    cur = con.cursor()
    if order == "mais_lidas":
        order_clause = "ORDER BY views DESC, created_at DESC"
    else:
        order_clause = "ORDER BY created_at DESC, id DESC"
    query = f"""
        SELECT id, titulo, imagem_url, resumo, texto_completo, link,
               autor, site, categoria, views, created_at, likes, liked
        FROM news
        WHERE titulo LIKE ? OR resumo LIKE ? OR texto_completo LIKE ?
        {order_clause}
        LIMIT ?
    """
    cur.execute(query, (term_like, term_like, term_like, limit))
    rows = cur.fetchall()
    con.close()
    out = []

    for r in rows:
        ts = r[10] or int(time.time())
        data_fmt = time.strftime("%d/%m/%Y %H:%M", time.localtime(ts))
        titulo = r[1]
        resumo = r[3] or ""

        def highlight(text):
            try:
                return re.sub(
                    re.escape(term),
                    lambda m: f"<mark>{m.group(0)}</mark>",
                    text,
                    flags=re.IGNORECASE,
                )
            except re.error:
                return text

        out.append(
            {
                "id": r[0],
                "titulo": titulo,
                "titulo_highlight": highlight(titulo),
                "imagem_url": r[2],
                "resumo": resumo,
                "resumo_highlight": highlight(resumo),
                "texto_completo": r[4],
                "link": r[5],
                "autor": r[6],
                "site": r[7],
                "categoria": r[8],
                "views": r[9],
                "data": data_fmt,
                "likes": r[11],
                "liked": r[12],
            }
        )
    return out


def clean_text(t):
    t = html_lib.unescape(t or "")
    return re.sub(r"\s+", " ", t).strip()


def strip_html(text):
    return clean_text(re.sub(r"<.*?>", " ", text or ""))


def fetch_html(url):
    try:
        r = requests.get(url, timeout=15, headers=UA_HEADER)
        r.raise_for_status()
        return html.fromstring(r.content)
    except Exception as e:
        print("Erro em fetch_html:", e)
        log_error("fetch_html", e)
        raise


def extract_article_generic(url, default_author, site_label):
    try:
        tree = fetch_html(url)
        paras = tree.xpath(
            "//article//p | //div[contains(@class,'content') or contains(@class,'texto') or contains(@class,'body') or contains(@id,'content')]//p"
        )
        textos = []
        for p in paras:
            txt = clean_text(p.text_content())
            if len(txt) > 40:
                textos.append(txt)
        texto = "\n\n".join(textos) if textos else ""
        autor = default_author
        meta_autor = tree.xpath("//meta[@name='author']/@content")
        if meta_autor:
            autor = clean_text(meta_autor[0])
        if not autor:
            autor = default_author
        img = None
        og_img = tree.xpath("//meta[@property='og:image']/@content")
        if og_img:
            img = og_img[0]
        if not img:
            img_tags = tree.xpath("//article//img/@src | //img[@class='featured']/@src")
            if img_tags:
                img = img_tags[0]
        return texto, autor, img
    except Exception as e:
        log_error(f"extract_article_generic_{site_label}", e)
        return "", default_author, None


def extract_full_article_g1(url):
    try:
        tree = fetch_html(url)
        paras = tree.xpath(
            "//div[contains(@class,'mc-article-body')]//p | //article//p"
        )
        textos = []
        for p in paras:
            txt = clean_text(p.text_content())
            if len(txt) > 40:
                textos.append(txt)
        texto = "\n\n".join(textos)
        autor = None
        meta_autor = tree.xpath("//meta[@name='author']/@content")
        if meta_autor:
            autor = clean_text(meta_autor[0])
        if not autor:
            autor_span = tree.xpath(
                "//span[contains(@class,'content-publication-data__from')]/text()"
            )
            if autor_span:
                autor = clean_text(" ".join(autor_span))
        if not autor:
            autor = "Redação G1"
        img = None
        og_img = tree.xpath("//meta[@property='og:image']/@content")
        if og_img:
            img = og_img[0]
        if not img:
            img_tags = tree.xpath("//article//img/@src")
            if img_tags:
                img = img_tags[0]
        return texto, autor, img
    except Exception as e:
        print("G1 erro ao extrair artigo:", e)
        log_error("extract_full_article_g1", e)
        return "", "Redação G1", None


def crawl_g1_musica(max_items=120, max_pages=8):
    print("G1 buscando notícias...")
    results = []
    page = 1
    while len(results) < max_items and page <= max_pages:
        if page == 1:
            url = G1_URL
        else:
            url = f"{G1_URL}?page={page}"
        print("G1 página", page, url)
        try:
            tree = fetch_html(url)
        except Exception as e:
            print("G1 erro ao baixar página:", e)
            log_error("crawl_g1_musica_fetch_page", e)
            break
        articles = tree.xpath("//div[contains(@class,'feed-post-body')]")
        if not articles:
            break
        for art in articles:
            try:
                titulo = clean_text(" ".join(art.xpath(".//a//text()")))
                link_list = art.xpath(".//a/@href")
                if not titulo or not link_list:
                    continue
                link = link_list[0]
                img_list = art.xpath(".//img/@src")
                imagem_url = img_list[0] if img_list else None
                resumo = clean_text(" ".join(art.xpath(".//p//text()")))
                texto_completo, autor, img_full = extract_full_article_g1(link)
                if img_full:
                    imagem_url = img_full
                if not resumo and texto_completo:
                    resumo = texto_completo
                if len(resumo) > 230:
                    resumo = resumo[:230].rsplit(" ", 1)[0] + "..."
                results.append(
                    {
                        "titulo": titulo,
                        "imagem_url": imagem_url,
                        "resumo": resumo,
                        "texto_completo": texto_completo,
                        "link": link,
                        "autor": autor,
                        "site": "G1 Música",
                    }
                )
            except Exception as e:
                print("G1 erro em um card:", e)
                log_error("crawl_g1_musica_card", e)
                continue
            if len(results) >= max_items:
                break
        page += 1
    print("G1 coletadas", len(results), "notícias.")
    return results


def crawl_popline(max_items=120, max_pages=5):
    print("Popline buscando notícias...")
    results = []
    page = 1
    while len(results) < max_items and page <= max_pages:
        url = POPLINE_URL if page == 1 else f"{POPLINE_URL}page/{page}/"
        print("Popline página", page, url)
        try:
            tree = fetch_html(url)
        except Exception as e:
            log_error("crawl_popline_fetch_page", e)
            break
        articles = tree.xpath("//article")
        if not articles:
            break
        for art in articles:
            try:
                titulo = clean_text(" ".join(art.xpath(".//h2//text()") or art.xpath(".//a//text()")))
                link_list = art.xpath(".//a/@href")
                link = link_list[0] if link_list else None
                if not titulo or not link:
                    continue
                img_list = art.xpath(".//img/@src")
                imagem_url = img_list[0] if img_list else None
                resumo = clean_text(" ".join(art.xpath(".//p//text()")))
                texto_completo, autor, img_full = extract_article_generic(link, "Portal POPline", "Popline")
                if img_full:
                    imagem_url = img_full
                if not resumo and texto_completo:
                    resumo = texto_completo
                if len(resumo) > 230:
                    resumo = resumo[:230].rsplit(" ", 1)[0] + "..."
                results.append(
                    {
                        "titulo": titulo,
                        "imagem_url": imagem_url,
                        "resumo": resumo,
                        "texto_completo": texto_completo,
                        "link": link,
                        "autor": autor,
                        "site": "Portal POPline",
                    }
                )
            except Exception as e:
                log_error("crawl_popline_card", e)
                continue
            if len(results) >= max_items:
                break
        page += 1
    print("Popline coletadas", len(results), "notícias.")
    return results


def crawl_tracklist(max_items=120, max_pages=5):
    print("Tracklist buscando notícias...")
    results = []
    page = 1
    while len(results) < max_items and page <= max_pages:
        url = TRACKLIST_URL if page == 1 else f"{TRACKLIST_URL}page/{page}/"
        print("Tracklist página", page, url)
        try:
            tree = fetch_html(url)
        except Exception as e:
            log_error("crawl_tracklist_fetch_page", e)
            break
        articles = tree.xpath("//article")
        if not articles:
            break
        for art in articles:
            try:
                titulo = clean_text(" ".join(art.xpath(".//h2//text()") or art.xpath(".//a//text()")))
                link_list = art.xpath(".//a/@href")
                link = link_list[0] if link_list else None
                if not titulo or not link:
                    continue
                img_list = art.xpath(".//img/@src")
                imagem_url = img_list[0] if img_list else None
                resumo = clean_text(" ".join(art.xpath(".//p//text()")))
                texto_completo, autor, img_full = extract_article_generic(link, "Tracklist", "Tracklist")
                if img_full:
                    imagem_url = img_full
                if not resumo and texto_completo:
                    resumo = texto_completo
                if len(resumo) > 230:
                    resumo = resumo[:230].rsplit(" ", 1)[0] + "..."
                results.append(
                    {
                        "titulo": titulo,
                        "imagem_url": imagem_url,
                        "resumo": resumo,
                        "texto_completo": texto_completo,
                        "link": link,
                        "autor": autor,
                        "site": "Tracklist",
                    }
                )
            except Exception as e:
                log_error("crawl_tracklist_card", e)
                continue
            if len(results) >= max_items:
                break
        page += 1
    print("Tracklist coletadas", len(results), "notícias.")
    return results


def crawl_all_sources():
    all_news = []
    all_news.extend(crawl_g1_musica(max_items=120, max_pages=8))
    all_news.extend(crawl_popline(max_items=120, max_pages=5))
    all_news.extend(crawl_tracklist(max_items=120, max_pages=5))
    save_news_batch(all_news)


HTML_INDEX = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<title>InMusic – Notícias de Música</title>
<style>
 body {
   font-family: Arial, Helvetica, sans-serif;
   background:#0f172a;
   margin:0;
   color:#e5e7eb;
 }
 header {
   background:#020617;
   color:#e5e7eb;
   padding:10px 24px;
   display:flex;
   justify-content:space-between;
   align-items:center;
   box-shadow:0 2px 8px rgba(0,0,0,0.5);
 }
 .logo-text {
   font-size:20px;
   font-weight:bold;
 }
 .nav-links a {
   color:#9ca3af;
   text-decoration:none;
   font-size:13px;
   margin-left:14px;
 }
 .nav-links a:hover {
   color:#e5e7eb;
 }
 .container {
   max-width:1200px;
   margin:24px auto 40px;
   padding:0 16px;
   display:grid;
   grid-template-columns: minmax(0, 3fr) minmax(240px, 1fr);
   gap:24px;
 }
 .section-title {
   margin:0 0 16px;
   font-size:18px;
   font-weight:bold;
 }
 .grid {
   display:grid;
   grid-template-columns:repeat(auto-fit,minmax(280px,1fr));
   gap:18px;
 }
 .card {
   background:#020617;
   border-radius:14px;
   overflow:hidden;
   box-shadow:0 10px 25px rgba(15,23,42,0.8);
   border:1px solid rgba(148,163,184,0.25);
   display:flex;
   flex-direction:column;
 }
 .thumb {
   width:100%;
   height:190px;
   overflow:hidden;
   background:#111827;
 }
 .thumb img {
   width:100%;
   height:100%;
   object-fit:cover;
   display:block;
 }
 .card-body {
   padding:10px 14px 14px;
   color:#e5e7eb;
 }
 .meta {
   font-size:11px;
   color:#9ca3af;
   margin-bottom:4px;
 }
 .meta span {
   margin-right:6px;
 }
 .title {
   font-size:15px;
   font-weight:bold;
   margin-bottom:6px;
 }
 .resumo {
   font-size:13px;
   color:#d1d5db;
   margin-bottom:10px;
   line-height:1.5;
 }
 .btn {
   display:inline-block;
   background:#0ea5e9;
   color:#0b1120;
   padding:6px 11px;
   font-size:13px;
   border-radius:999px;
   text-decoration:none;
   font-weight:600;
 }
 .btn:hover {
   background:#38bdf8;
 }
 .sidebar {
   background:#020617;
   border-radius:14px;
   border:1px solid rgba(148,163,184,0.3);
   box-shadow:0 8px 20px rgba(15,23,42,0.7);
   padding:14px 14px 16px;
 }
 .sidebar h3 {
   margin:0 0 8px;
   font-size:15px;
 }
 .mais-lidas-list {
   list-style:none;
   padding:0;
   margin:0;
   font-size:13px;
 }
 .mais-lidas-list li {
   margin-bottom:8px;
 }
 .mais-lidas-list a {
   color:#e5e7eb;
   text-decoration:none;
 }
 .mais-lidas-list a:hover {
   text-decoration:underline;
 }
 .mais-lidas-views {
   font-size:11px;
   color:#9ca3af;
 }
 .categoria-label {
   display:inline-block;
   font-size:10px;
   padding:2px 6px;
   border-radius:999px;
   border:1px solid #4b5563;
   margin-left:4px;
 }
 .likes-tag {
   font-size:11px;
   color:#facc15;
 }
</style>
</head>
<body>

<header>
  <div class="logo-text">InMusic</div>
  <div class="nav-links">
    <a href="/">Início</a>
    <a href="/buscar">Pesquisar</a>
    <a href="/curtidas">Curtidas</a>
    <a href="/atualizar">Atualizar notícias</a>
  </div>
</header>

<div class="container">
  <div>
    {% if titulo_lista %}
      <h2 class="section-title">{{ titulo_lista }}</h2>
    {% endif %}
    <div class="grid">
      {% for n in noticias %}
        <div class="card">
          <div class="thumb">
            {% if n.imagem_url %}
              <img src="{{ n.imagem_url }}" alt="">
            {% endif %}
          </div>
          <div class="card-body">
            <div class="meta">
              <span>{{ n.site or 'Música' }}</span>
              {% if n.autor %}<span>• {{ n.autor }}</span>{% endif %}
              <span>• {{ n.data }}</span>
              {% if n.categoria %}
                <span class="categoria-label">{{ n.categoria }}</span>
              {% endif %}
              <span class="likes-tag">• {{ n.likes }} curtidas</span>
            </div>
            <div class="title">{{ n.titulo }}</div>
            <div class="resumo">{{ n.resumo }}</div>
            <a class="btn" href="/noticia/{{ n.id }}">Ver mais</a>
          </div>
        </div>
      {% endfor %}
    </div>
  </div>

  <aside class="sidebar">
    <h3>Mais lidas</h3>
    <ul class="mais-lidas-list">
      {% for m in mais_lidas %}
        <li>
          <a href="/noticia/{{ m.id }}">{{ m.titulo }}</a><br>
          <span class="mais-lidas-views">{{ m.views }} visualizações</span>
        </li>
      {% endfor %}
    </ul>
  </aside>
</div>

</body>
</html>
"""

HTML_NOTICIA = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<title>{{ titulo }}</title>
<style>
 body {
   margin:0;
   font-family: Arial, Helvetica, sans-serif;
   background:#0f172a;
   color:#e5e7eb;
 }
 header {
   background:#020617;
   color:#e5e7eb;
   padding:10px 24px;
   display:flex;
   justify-content:space-between;
   align-items:center;
   box-shadow:0 2px 8px rgba(0,0,0,0.6);
   position:sticky;
   top:0;
   z-index:10;
 }
 .logo-text {
   font-size:18px;
   font-weight:bold;
 }
 header .right {
   display:flex;
   align-items:center;
   gap:12px;
   font-size:12px;
   color:#9ca3af;
 }
 .btn-back {
   padding:6px 12px;
   border-radius:999px;
   border:1px solid #38bdf8;
   background:transparent;
   color:#e5e7eb;
   text-decoration:none;
   font-size:12px;
   font-weight:600;
 }
 .btn-back:hover {
   background:#0ea5e9;
   color:#0b1120;
 }
 .btn-like {
   padding:6px 12px;
   border-radius:999px;
   border:1px solid #f97316;
   background:#f97316;
   color:#0b1120;
   font-size:12px;
   font-weight:600;
   cursor:pointer;
 }
 .btn-like.liked {
   background:#22c55e;
   border-color:#22c55e;
 }
 .page {
   max-width:1200px;
   margin:28px auto 40px;
   padding:0 20px;
   display:grid;
   grid-template-columns: minmax(260px, 380px) minmax(0, 1fr);
   gap:32px;
 }
 .image-panel {
   background:#020617;
   border-radius:24px;
   padding:16px;
   box-shadow:0 18px 40px rgba(15,23,42,0.9);
   border:1px solid rgba(148,163,184,0.3);
 }
 .image-panel img {
   width:100%;
   border-radius:18px;
   display:block;
   margin-bottom:14px;
 }
 .meta-block {
   font-size:13px;
   color:#9ca3af;
   line-height:1.6;
 }
 .meta-label {
   font-size:11px;
   text-transform:uppercase;
   letter-spacing:1px;
   color:#6b7280;
 }
 .content-panel {
   background:#020617;
   border-radius:18px;
   padding:22px 24px 26px;
   box-shadow:0 15px 35px rgba(15,23,42,0.8);
   border:1px solid rgba(148,163,184,0.3);
 }
 .news-title {
   font-size:22px;
   font-weight:bold;
   margin-bottom:8px;
 }
 .news-meta-top {
   font-size:12px;
   color:#9ca3af;
   margin-bottom:12px;
 }
 .news-body p {
   margin-bottom:14px;
   line-height:1.8;
   font-size:15px;
   text-align:justify;
   color:#e5e7eb;
 }
 .news-body p:first-child {
   margin-top:4px;
 }
 .comments {
   max-width:1200px;
   margin:0 auto 40px;
   padding:0 20px;
 }
 .comments-title {
   font-size:18px;
   margin-bottom:12px;
 }
 .comment-card {
   background:#020617;
   border-radius:12px;
   padding:10px 14px;
   border:1px solid rgba(148,163,184,0.3);
   margin-bottom:10px;
 }
 .comment-meta {
   font-size:11px;
   color:#9ca3af;
   margin-bottom:4px;
 }
 .comment-text {
   font-size:14px;
   line-height:1.6;
 }
 .comment-form {
   margin-top:18px;
   background:#020617;
   border-radius:12px;
   padding:12px 14px 14px;
   border:1px solid rgba(148,163,184,0.3);
 }
 .comment-form label {
   display:block;
   font-size:13px;
   margin-bottom:4px;
 }
 .comment-form input,
 .comment-form textarea {
   width:100%;
   padding:7px 9px;
   border-radius:8px;
   border:1px solid #4b5563;
   background:#020617;
   color:#e5e7eb;
   font-size:13px;
   margin-bottom:8px;
 }
 .comment-form button {
   padding:7px 14px;
   border-radius:999px;
   border:none;
   background:#0ea5e9;
   color:#0b1120;
   font-size:13px;
   font-weight:600;
   cursor:pointer;
 }
 .comment-form button:hover {
   background:#38bdf8;
 }
 @media (max-width:900px) {
   .page {
     grid-template-columns:1fr;
   }
 }
</style>
<script>
function updateClock(){
  const el = document.getElementById('relogio');
  if(!el) return;
  const d = new Date();
  const opts = { weekday:'short', day:'2-digit', month:'2-digit', year:'numeric',
                 hour:'2-digit', minute:'2-digit', second:'2-digit' };
  el.textContent = d.toLocaleString('pt-BR', opts);
}
setInterval(updateClock, 1000);
window.onload = updateClock;
</script>
</head>
<body>

<header>
  <div class="logo-text">InMusic</div>
  <div class="right">
    <span id="relogio"></span>
    <form method="post" action="/curtir/{{ news_id }}" style="margin:0;">
      <button type="submit" class="btn-like {% if liked %}liked{% endif %}">
        {% if liked %}💙 Curtida{% else %}♡ Curtir{% endif %} ({{ likes }})
      </button>
    </form>
    <a class="btn-back" href="/">← Voltar ao início</a>
  </div>
</header>

<div class="page">
  <div class="image-panel">
    {% if imagem_url %}
      <img src="{{ imagem_url }}" alt="Imagem da notícia">
    {% endif %}
    <div class="meta-block">
      <div class="meta-label">Autor</div>
      <div>{{ autor }}</div>
      <div style="height:10px;"></div>
      <div class="meta-label">Fonte</div>
      <div>{{ site }}</div>
      <div style="height:10px;"></div>
      <div class="meta-label">Categoria</div>
      <div>{{ categoria }}</div>
      <div style="height:10px;"></div>
      <div class="meta-label">Coletada em</div>
      <div>{{ data }}</div>
      <div style="height:10px;"></div>
      <div class="meta-label">Visualizações</div>
      <div>{{ views }}</div>
    </div>
  </div>

  <div class="content-panel">
    <div class="news-title">{{ titulo }}</div>
    <div class="news-meta-top">
      Notícia exibida pelo InMusic.
    </div>
    <div class="news-body">
      {% for p in paragrafos %}
        <p>{{ p }}</p>
      {% endfor %}
    </div>
  </div>
</div>

<div class="comments" id="comentarios">
  <div class="comments-title">Comentários</div>

  {% if comentarios %}
    {% for c in comentarios %}
      <div class="comment-card">
        <div class="comment-meta">{{ c.nome }} • {{ c.data }}</div>
        <div class="comment-text">{{ c.texto }}</div>
      </div>
    {% endfor %}
  {% else %}
    <div class="comment-card">
      <div class="comment-text">Ainda não há comentários. Seja o primeiro a comentar!</div>
    </div>
  {% endif %}

  <div class="comment-form">
    <form method="post" action="/noticia/{{ news_id }}/comentar">
      <label for="nome">Seu nome (opcional)</label>
      <input type="text" id="nome" name="nome" placeholder="Digite seu nome">
      <label for="texto">Comentário</label>
      <textarea id="texto" name="texto" rows="4" placeholder="O que você achou dessa notícia?"></textarea>
      <button type="submit">Enviar comentário</button>
    </form>
  </div>
</div>

</body>
</html>
"""

HTML_SEARCH = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<title>Pesquisar – InMusic</title>
<style>
 body {
   font-family: Arial, Helvetica, sans-serif;
   background:#0f172a;
   margin:0;
   color:#e5e7eb;
 }
 header {
   background:#020617;
   color:#e5e7eb;
   padding:10px 24px;
   display:flex;
   justify-content:space-between;
   align-items:center;
   box-shadow:0 2px 8px rgba(0,0,0,0.5);
 }
 .logo-text {
   font-size:20px;
   font-weight:bold;
 }
 .nav-links a {
   color:#9ca3af;
   text-decoration:none;
   font-size:13px;
   margin-left:14px;
 }
 .nav-links a:hover {
   color:#e5e7eb;
 }
 .container {
   max-width:1100px;
   margin:28px auto 40px;
   padding:0 16px;
 }
 .search-box {
   margin-bottom:24px;
   background:#020617;
   padding:16px 18px;
   border-radius:12px;
   border:1px solid rgba(148,163,184,0.35);
 }
 .search-box form {
   display:flex;
   gap:10px;
   flex-wrap:wrap;
 }
 .search-box input[type="text"] {
   flex:1;
   min-width:200px;
   padding:8px 10px;
   border-radius:8px;
   border:1px solid #4b5563;
   background:#020617;
   color:#e5e7eb;
 }
 .search-box select {
   padding:8px 10px;
   border-radius:8px;
   border:1px solid #4b5563;
   background:#020617;
   color:#e5e7eb;
   font-size:13px;
 }
 .search-box button {
   padding:8px 18px;
   border:none;
   border-radius:999px;
   background:#0ea5e9;
   color:#0b1120;
   font-weight:600;
   cursor:pointer;
 }
 .search-box button:hover {
   background:#38bdf8;
 }
 .msg {
   margin-top:12px;
   font-size:13px;
   color:#9ca3af;
 }
 .grid {
   display:grid;
   grid-template-columns:repeat(auto-fit,minmax(320px,1fr));
   gap:20px;
 }
 .card {
   background:#020617;
   border-radius:14px;
   overflow:hidden;
   box-shadow:0 10px 25px rgba(15,23,42,0.8);
   border:1px solid rgba(148,163,184,0.25);
   display:flex;
   flex-direction:column;
 }
 .thumb {
   width:100%;
   height:190px;
   overflow:hidden;
   background:#111827;
 }
 .thumb img {
   width:100%;
   height:100%;
   object-fit:cover;
   display:block;
 }
 .card-body {
   padding:14px 16px 16px;
   color:#e5e7eb;
 }
 .meta {
   font-size:11px;
   color:#9ca3af;
   margin-bottom:4px;
 }
 .meta span {
   margin-right:6px;
 }
 .title {
   font-size:16px;
   font-weight:bold;
   margin-bottom:8px;
 }
 .title mark, .resumo mark {
   background:#facc15;
   color:#0b1120;
 }
 .resumo {
   font-size:13px;
   color:#d1d5db;
   margin-bottom:12px;
   line-height:1.5;
 }
 .btn {
   display:inline-block;
   background:#0ea5e9;
   color:#0b1120;
   padding:7px 12px;
   font-size:13px;
   border-radius:999px;
   text-decoration:none;
   font-weight:600;
 }
 .btn:hover {
   background:#38bdf8;
 }
 .categoria-label {
   display:inline-block;
   font-size:10px;
   padding:2px 6px;
   border-radius:999px;
   border:1px solid #4b5563;
   margin-left:4px;
 }
 .likes-tag {
   font-size:11px;
   color:#facc15;
 }
</style>
</head>
<body>

<header>
  <div class="logo-text">InMusic</div>
  <div class="nav-links">
    <a href="/">Início</a>
    <a href="/buscar">Pesquisar</a>
    <a href="/curtidas">Curtidas</a>
  </div>
</header>

<div class="container">
  <div class="search-box">
    <form method="get" action="/buscar">
      <input type="text" name="q" placeholder="Buscar por artista, música, álbum..." value="{{ termo }}">
      <select name="ordem">
        <option value="recentes" {% if ordem == 'recentes' %}selected{% endif %}>Mais recentes</option>
        <option value="mais_lidas" {% if ordem == 'mais_lidas' %}selected{% endif %}>Mais lidas</option>
      </select>
      <button type="submit">Pesquisar</button>
    </form>
    {% if termo %}
      <div class="msg">Resultados para: <strong>{{ termo }}</strong> ({{ total }} encontrados)</div>
    {% else %}
      <div class="msg">Digite um termo e clique em Pesquisar.</div>
    {% endif %}
  </div>

  {% if resultados %}
  <div class="grid">
    {% for n in resultados %}
      <div class="card">
        <div class="thumb">
          {% if n.imagem_url %}
            <img src="{{ n.imagem_url }}" alt="">
          {% endif %}
        </div>
        <div class="card-body">
          <div class="meta">
            <span>{{ n.site or 'Música' }}</span>
            {% if n.autor %}<span>• {{ n.autor }}</span>{% endif %}
            <span>• {{ n.data }}</span>
            {% if n.categoria %}
              <span class="categoria-label">{{ n.categoria }}</span>
            {% endif %}
            <span class="likes-tag">• {{ n.likes }} curtidas</span>
          </div>
          <div class="title">{{ n.titulo_highlight|safe }}</div>
          <div class="resumo">{{ n.resumo_highlight|safe }}</div>
          <a class="btn" href="/noticia/{{ n.id }}">Ver mais</a>
        </div>
      </div>
    {% endfor %}
  </div>
  {% elif termo %}
    <div class="msg">Nenhuma notícia encontrada para esse termo.</div>
  {% endif %}
</div>

</body>
</html>
"""

app = Flask(__name__)


@app.route("/")
def index():
    total = count_news()
    if total <= 0:
        noticias = []
    else:
        noticias = load_news(limit=min(total, 200), offset=0)
    mais_lidas = load_most_viewed(limit=5)
    return render_template_string(
        HTML_INDEX,
        noticias=noticias,
        mais_lidas=mais_lidas,
        total=total,
        titulo_lista="Últimas notícias",
    )


@app.route("/curtidas")
def curtidas():
    noticias = load_liked(limit=200)
    mais_lidas = load_most_viewed(limit=5)
    return render_template_string(
        HTML_INDEX,
        noticias=noticias,
        mais_lidas=mais_lidas,
        total=len(noticias),
        titulo_lista="Minhas notícias curtidas",
    )


@app.route("/buscar")
def buscar():
    termo = request.args.get("q", "").strip()
    ordem = request.args.get("ordem", "recentes")
    resultados = []
    total = 0
    if termo:
        resultados = search_news(termo, limit=200, order=ordem)
        total = len(resultados)
    return render_template_string(
        HTML_SEARCH,
        termo=termo,
        resultados=resultados,
        total=total,
        ordem=ordem,
    )


@app.route("/noticia/<int:id_>")
def noticia(id_):
    increment_views(id_)
    n = load_one(id_)
    if not n:
        return "Notícia não encontrada."
    texto = n["texto_completo"] or ""
    paragrafos = [p.strip() for p in texto.split("\n\n") if p.strip()]
    if not paragrafos and texto:
        paragrafos = [texto]
    if not paragrafos and n.get("resumo"):
        paragrafos = [n["resumo"]]
    comentarios = load_comments(id_)
    return render_template_string(
        HTML_NOTICIA,
        news_id=id_,
        titulo=n["titulo"],
        imagem_url=n["imagem_url"],
        autor=n.get("autor") or "Redação",
        site=n.get("site") or "Música",
        categoria=n.get("categoria") or "Outros",
        data=n.get("data") or "",
        views=n.get("views") or 0,
        likes=n.get("likes") or 0,
        liked=n.get("liked") or 0,
        paragrafos=paragrafos,
        comentarios=comentarios,
    )


@app.route("/noticia/<int:id_>/comentar", methods=["POST"])
def comentar(id_):
    nome = request.form.get("nome", "")
    texto = request.form.get("texto", "")
    add_comment(id_, nome, texto)
    return redirect(f"/noticia/{id_}#comentarios")


@app.route("/curtir/<int:id_>", methods=["POST"])
def curtir(id_):
    toggle_like(id_)
    referer = request.headers.get("Referer") or f"/noticia/{id_}"
    return redirect(referer)


@app.route("/atualizar")
def atualizar():
    try:
        crawl_all_sources()
        return redirect("/")
    except Exception as e:
        log_error("rota_atualizar", e)
        return "Erro ao atualizar notícias."


@app.route("/admin/log")
def admin_log():
    if not os.path.exists(LOG_PATH):
        conteudo = "Sem logs ainda."
    else:
        with open(LOG_PATH, "r", encoding="utf-8") as f:
            texto = f.read()
        if len(texto) > 4000:
            texto = texto[-4000:]
        conteudo = texto
    return f"<pre>{conteudo}</pre>"


if __name__ == "__main__":
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    init_db()
    print("Coletando notícias iniciais (G1, POPline, Tracklist)...")
    try:
        crawl_all_sources()
    except Exception as e:
        log_error("main_crawler_inicial", e)
    print(f"Banco agora tem {count_news()} notícias")
    print("Rodando em http://127.0.0.1:5000")
    app.run(debug=True)
