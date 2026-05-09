import os
import re
import time
import logging
import asyncio
import urllib.request
from datetime import datetime

import feedparser
import schedule
from telegram import Bot
from telegram.constants import ParseMode

try:
    import trafilatura
    TRAFILATURA_AVAILABLE = True
except ImportError:
    TRAFILATURA_AVAILABLE = False
    logging.warning("trafilatura не установлен. Запусти: pip install trafilatura")

# ============================================================
#  НАСТРОЙКИ
# ============================================================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = "213900350"

SEND_TIME_MORNING = "03:00"  # 06:00 по Москве (UTC+3 = UTC-0 смещение -3ч)
SEND_TIME_EVENING = "17:00"  # 20:00 по Москве

SUMMARY_LENGTH = 500
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ============================================================
#  RSS-источники
# ============================================================
FEEDS = {
    "tech": {
        "label": "Tech",
        "emoji": "💻",
        "count": 4,
        "urls": [
            "https://feeds.arstechnica.com/arstechnica/technology-lab",
            "https://www.theverge.com/rss/index.xml",
            "https://techcrunch.com/feed/",
            "https://www.wired.com/feed/rss",
        ],
    },
    "politics": {
        "label": "Politics",
        "emoji": "🌍",
        "count": 2,
        "urls": [
            "https://feeds.bbci.co.uk/news/world/rss.xml",
            "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
        ],
    },
    "finance": {
        "label": "Finance & Crypto",
        "emoji": "📈",
        "count": 2,
        "urls": [
            "https://feeds.bloomberg.com/markets/news.rss",
            "https://cointelegraph.com/rss",
            "https://www.coindesk.com/arc/outboundfeeds/rss/",
        ],
    },
    "entertainment": {
        "label": "Movies & Games",
        "emoji": "🎬",
        "count": 2,
        "urls": [
            "https://www.eurogamer.net/feed",
            "https://www.rockpapershotgun.com/feed",
            "https://www.hollywoodreporter.com/feed/",
            "https://collider.com/feed/",
        ],
    },
}


def get_summary_from_url(url):
    if not TRAFILATURA_AVAILABLE:
        return ""
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read()
        text = trafilatura.extract(html, include_comments=False, include_tables=False)
        if not text:
            return ""
        text = text.strip()
        if len(text) > SUMMARY_LENGTH:
            text = text[:SUMMARY_LENGTH]
            last_dot = max(text.rfind("."), text.rfind("!"), text.rfind("?"))
            if last_dot > 100:
                text = text[:last_dot + 1]
            else:
                text = text.rstrip() + "..."
        return text
    except Exception as e:
        log.warning(f"Не удалось получить текст {url}: {e}")
        return ""


def clean_html(text):
    return re.sub(r"<[^>]+>", "", text).strip()


def escape_md(text):
    for ch in ["*", "_", "`", "["]:
        text = text.replace(ch, "\\" + ch)
    return text


def trim_summary(text):
    if len(text) <= SUMMARY_LENGTH:
        return text
    text = text[:SUMMARY_LENGTH]
    last_dot = max(text.rfind("."), text.rfind("!"), text.rfind("?"))
    if last_dot > 100:
        return text[:last_dot + 1]
    return text.rstrip() + "..."


def fetch_articles(urls, count):
    articles = []
    seen_titles = set()
    for url in urls:
        if len(articles) >= count:
            break
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries:
                if len(articles) >= count:
                    break
                title = entry.get("title", "").strip()
                link = entry.get("link", "").strip()
                if not title or not link or title in seen_titles:
                    continue
                seen_titles.add(title)
                rss_summary = clean_html(
                    entry.get("summary", "") or entry.get("description", "")
                )
                articles.append({
                    "title": title,
                    "link": link,
                    "rss_summary": trim_summary(rss_summary),
                })
        except Exception as e:
            log.warning(f"Не удалось получить {url}: {e}")
    return articles


def build_message():
    today = datetime.now().strftime("%A, %B %d %Y")
    messages = [f"📰 *Your Daily News Digest*\n_{today}_"]

    for category, cfg in FEEDS.items():
        log.info(f"Загружаю категорию: {cfg['label']}...")
        articles = fetch_articles(cfg["urls"], cfg["count"])
        section = f"\n\n{cfg['emoji']} *{cfg['label']}*\n" + "─" * 20

        if not articles:
            section += "\n_Не удалось загрузить новости_"
        else:
            for i, art in enumerate(articles, 1):
                title = escape_md(art["title"])
                summary = art["rss_summary"]
                if not summary:
                    log.info(f"  Получаю текст со страницы: {art['title'][:50]}...")
                    summary = get_summary_from_url(art["link"])
                summary_text = escape_md(summary) if summary else "_Краткое описание недоступно_"
                section += (
                    f"\n\n*{i}. {title}*\n"
                    f"{summary_text}\n"
                    f"[Читать полностью]({art['link']})"
                )

        messages.append(section)

    messages.append("\n\n_Powered by your personal news bot_ 🤖")
    return "".join(messages)


async def send_news():
    log.info("Отправляю новости...")
    try:
        bot = Bot(token=BOT_TOKEN)
        text = build_message()
        max_len = 4096

        if len(text) <= max_len:
            await bot.send_message(
                chat_id=CHAT_ID,
                text=text,
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=True,
            )
        else:
            parts = text.split("\n\n")
            chunk = ""
            for part in parts:
                if len(chunk) + len(part) + 2 > max_len:
                    await bot.send_message(
                        chat_id=CHAT_ID,
                        text=chunk,
                        parse_mode=ParseMode.MARKDOWN,
                        disable_web_page_preview=True,
                    )
                    await asyncio.sleep(1)
                    chunk = part
                else:
                    chunk += "\n\n" + part if chunk else part
            if chunk:
                await bot.send_message(
                    chat_id=CHAT_ID,
                    text=chunk,
                    parse_mode=ParseMode.MARKDOWN,
                    disable_web_page_preview=True,
                )

        log.info("Новости успешно отправлены!")
    except Exception as e:
        log.error(f"Ошибка при отправке: {e}")


def job():
    asyncio.run(send_news())


if __name__ == "__main__":
    log.info(f"Бот запущен. Новости в {SEND_TIME_MORNING} и {SEND_TIME_EVENING} UTC.")

    schedule.every().day.at(SEND_TIME_MORNING).do(job)
    schedule.every().day.at(SEND_TIME_EVENING).do(job)

    while True:
        schedule.run_pending()
        time.sleep(30)
