#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=====================================================================
 ПВБ (Поволжский банк) — прототип бронирования помещений для юрлиц
=====================================================================
 Запуск:        python app.py
 Адрес:         http://127.0.0.1:5000
 Зависимостей:  НЕТ (только стандартная библиотека Python)
 База данных:   SQLite, файл pvb.db создаётся автоматически
                 при первом запуске и заполняется демо-данными.
=====================================================================
"""
import html
import os
import sqlite3
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "pvb.db")
STATIC_DIR = os.path.join(BASE_DIR, "static")

WORK_START = 9      # рабочие часы помещений: с 9:00
WORK_END = 20       #                            до 20:00

# ---------------------------------------------------------------- БД ----
SCHEMA = """
CREATE TABLE cities (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL
);
CREATE TABLE buildings (
    id      INTEGER PRIMARY KEY,
    city_id INTEGER NOT NULL REFERENCES cities(id),
    name    TEXT NOT NULL,
    address TEXT NOT NULL
);
CREATE TABLE rooms (
    id                  INTEGER PRIMARY KEY,
    building_id         INTEGER NOT NULL REFERENCES buildings(id),
    name                TEXT NOT NULL,
    area                REAL NOT NULL,             -- метраж, м²
    capacity            INTEGER NOT NULL,          -- вместимость, чел.
    tech_characteristics TEXT NOT NULL,            -- тех. характеристики
    equipment           TEXT NOT NULL,             -- оборудование
    price_per_hour      INTEGER NOT NULL,          -- ₽/час
    skud_required       INTEGER NOT NULL DEFAULT 0, -- нужна ли проходка (СКУД)
    photo               TEXT NOT NULL              -- заглушка фото
);
CREATE TABLE bookings (
    id            INTEGER PRIMARY KEY,
    room_id       INTEGER NOT NULL REFERENCES rooms(id),
    date          TEXT NOT NULL,                   -- YYYY-MM-DD
    start_time    INTEGER NOT NULL,                -- час начала (9..19)
    end_time      INTEGER NOT NULL,                -- час окончания (10..20)
    company_name  TEXT NOT NULL,                   -- название юрлица
    contact_name  TEXT NOT NULL,
    contact_phone TEXT NOT NULL,
    contact_email TEXT,
    comment       TEXT,
    status        TEXT NOT NULL DEFAULT 'active',  -- active / cancelled
    created_at    TEXT NOT NULL
);
CREATE TABLE participants (
    id            INTEGER PRIMARY KEY,
    booking_id    INTEGER NOT NULL REFERENCES bookings(id),
    full_name     TEXT NOT NULL,
    passport_ser  TEXT NOT NULL,
    passport_num  TEXT NOT NULL,
    position      TEXT
);
"""

# Демо-данные (потом заменятся реальными)
CITIES = ["Ульяновск", "Самара", "Пенза", "Тольятти"]

BUILDINGS = {
    "Ульяновск": [
        ("Головное отделение ПВБ", "г. Ульяновск, ул. Ленина, д. 73"),
        ("Административное здание № 2", "г. Ульяновск, ул. Федерации, д. 12"),
    ],
    "Самара": [
        ("Головное отделение ПВБ", "г. Самара, ул. Куйбышева, д. 131"),
        ("Дополнительный офис «Самара-Центр»", "г. Самара, ул. Ленинградская, д. 70"),
    ],
    "Пенза": [
        ("Головное отделение ПВБ", "г. Пенза, ул. Московская, д. 59"),
    ],
    "Тольятти": [
        ("Дополнительный офис «Тольятти»", "г. Тольятти, б-р Королёва, д. 11"),
    ],
}

ROOM_TYPES = [
    # (название, метраж, вместимость, ₽/час, требуется СКУД, фото-заглушка)
    ("Переговорная комната", 28, 6, 500, 0, "room1.png"),
    ("Конференц-зал",        55, 20, 900, 1, "room2.png"),
    ("Офисное помещение",    38, 4, 650, 0, "room3.png"),
    ("Зал для встреч",       45, 12, 750, 1, "room4.png"),
]

TECH = [
    "Отделка: ковролин, натяжные потолки; кондиционер; естественное освещение",
    "Ремонт класса B; приточно-вытяжная вентиляция; кондиционер; окна во двор",
    "Современная отделка; сплит-система; blackout-шторы; электронные замки",
]
EQUIP = [
    "Стол конференц., 8 кресел, ТВ-панель 55\", флипчарт, Wi-Fi",
    "Проектор, экран, звуковая система, микрофоны, столы-банкетки до 20 чел.",
    "2 рабочих стола, кресла, шкаф, принтер (по запросу), Wi-Fi",
    "Круглый стол на 12 чел., ТВ-панель 65\", видеоконференцсвязь, флипчарт",
]


def get_db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    """Создаёт БД и наполняет демо-данными (только при первом запуске)."""
    if os.path.exists(DB_PATH):
        return
    db = get_db()
    db.executescript(SCHEMA)
    for city in CITIES:
        cur = db.execute("INSERT INTO cities (name) VALUES (?)", (city,))
        city_id = cur.lastrowid
        for bname, addr in BUILDINGS[city]:
            cur = db.execute(
                "INSERT INTO buildings (city_id, name, address) VALUES (?,?,?)",
                (city_id, bname, addr))
            bld_id = cur.lastrowid
            for i, (rname, area, cap, price, skud, photo) in enumerate(ROOM_TYPES):
                db.execute(
                    """INSERT INTO rooms (building_id, name, area, capacity,
                                          tech_characteristics, equipment,
                                          price_per_hour, skud_required, photo)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (bld_id, f"{rname} «{chr(1040+i)}»", area, cap,
                     TECH[i % len(TECH)], EQUIP[i % len(EQUIP)],
                     price, skud, photo))
    # Демо-бронирование на завтра, чтобы в календаре было видно занятые слоты
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    cur = db.execute(
        """INSERT INTO bookings (room_id, date, start_time, end_time,
                                 company_name, contact_name, contact_phone,
                                 contact_email, comment, status, created_at)
           VALUES (1, ?, 10, 13, 'ООО «Ромашка»', 'Иванов И.И.',
                   '+7 (900) 123-45-67', 'office@romashka.ru',
                   'Демо-запись', 'active', ?)""",
        (tomorrow, datetime.now().isoformat(timespec="seconds")))
    bid = cur.lastrowid
    db.execute(
        """INSERT INTO participants (booking_id, full_name, passport_ser,
                                     passport_num, position)
           VALUES (?,?,?,?,?)""",
        (bid, "Иванов Иван Иванович", "7301", "123456", "Директор"))
    db.execute(
        """INSERT INTO participants (booking_id, full_name, passport_ser,
                                     passport_num, position)
           VALUES (?,?,?,?,?)""",
        (bid, "Петрова Анна Сергеевна", "7302", "654321", "Бухгалтер"))
    db.commit()
    db.close()


# ------------------------------------------------------------ помощники ----
def esc(s):
    return html.escape(str(s if s is not None else ""))


def occupied_hours(db, room_id, day):
    """Множество занятых часов (9..19) для помещения в указанный день."""
    rows = db.execute(
        """SELECT start_time, end_time FROM bookings
           WHERE room_id=? AND date=? AND status='active'""",
        (room_id, day)).fetchall()
    occ = set()
    for r in rows:
        occ.update(range(r["start_time"], r["end_time"]))
    return occ


def has_overlap(db, room_id, day, start, end):
    return db.execute(
        """SELECT 1 FROM bookings
           WHERE room_id=? AND date=? AND status='active'
             AND start_time < ? AND end_time > ?""",
        (room_id, day, end, start)).fetchone() is not None


# ------------------------------------------------------- HTML-страницы ----
def page(title, body):
    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} — Бронирование помещений ПВБ</title>
<link rel="stylesheet" href="/static/css/style.css">
</head>
<body>
<header class="topbar">
  <div class="container topbar-inner">
    <a class="logo" href="/">
      <span class="logo-mark">ПВБ</span>
      <span class="logo-text">Бронирование помещений<small>для юридических лиц</small></span>
    </a>
    <nav class="nav">
      <a href="/">Города</a>
      <a href="/bookings">Все бронирования</a>
    </nav>
  </div>
</header>
<main class="container">
{body}
</main>
<footer class="footer">
  <div class="container">Прототип · данные сгенерированы для демонстрации · фото и схемы — заглушки</div>
</footer>
</body>
</html>"""


def flashes(qs):
    out = ""
    if "ok" in qs:
        out += f'<div class="flash flash-ok">✔ {esc(qs["ok"][0])}</div>'
    if "err" in qs:
        out += f'<div class="flash flash-err">✖ {esc(qs["err"][0])}</div>'
    return out


def page_cities(qs):
    db = get_db()
    cities = db.execute("""
        SELECT c.id, c.name,
               (SELECT COUNT(*) FROM buildings b WHERE b.city_id=c.id) AS n_bld,
               (SELECT COUNT(*) FROM rooms r JOIN buildings b ON b.id=r.building_id
                 WHERE b.city_id=c.id) AS n_rooms
        FROM cities c ORDER BY c.name""").fetchall()
    cards = "".join(
        f"""<a class="card city-card" href="/city?id={c["id"]}"><h3>{esc(c["name"])}</h3>
              <p>Административных зданий: <b>{c["n_bld"]}</b><br>
                 Помещений: <b>{c["n_rooms"]}</b></p>
              <span class="btn btn-green">Выбрать здание →</span></a>"""
        for c in cities)
    db.close()
    body = f"""<h1>Выбор города</h1>
<p class="muted">Сервис бронирования помещений в административных зданиях ПВБ.</p>
{flashes(qs)}
<div class="grid">{cards}</div>"""
    return page("Города", body)


def page_city(city_id, qs):
    db = get_db()
    city = db.execute("SELECT * FROM cities WHERE id=?", (city_id,)).fetchone()
    if not city:
        db.close()
        return page("Ошибка", "<h1>Город не найден</h1>")
    blds = db.execute("""
        SELECT b.*, (SELECT COUNT(*) FROM rooms r WHERE r.building_id=b.id) AS n_rooms
        FROM buildings b WHERE b.city_id=? ORDER BY b.name""", (city_id,)).fetchall()
    cards = "".join(
        f"""<div class="card building-card"><img src="/static/img/building.png" alt="Фото здания (заглушка)">
              <div class="building-info"><h3>{esc(b["name"])}</h3>
                <p class="muted">📍 {esc(b["address"])}</p>
                <p>Помещений в здании: <b>{b["n_rooms"]}</b></p>
                <a class="btn btn-green" href="/building?id={b["id"]}">Смотреть помещения →</a>
              </div></div>"""
        for b in blds)
    db.close()
    body = f"""<p class="crumbs"><a href="/">Города</a> / {esc(city["name"])}</p>
<h1>{esc(city["name"])}: административные здания</h1>
<div class="stack">{cards}</div>"""
    return page(city["name"], body)


def room_card(r, bname, city_name):
    skud = ('<span class="badge badge-red">Нужна проходка (СКУД)</span>'
            if r["skud_required"]
            else '<span class="badge badge-green">Открытое посещение</span>')
    return f"""<div class="card room-card"><img src="/static/img/{esc(r["photo"])}" alt="Фото помещения (заглушка)">
      <div class="room-info"><h3>{esc(r["name"])}</h3>
        <p class="muted">{esc(bname)} · {esc(city_name)}</p>
        <p class="room-meta">▦ {r["area"]:g} м² · 👥 до {r["capacity"]} чел. · {skud}</p>
        <p><b>Оборудование:</b> {esc(r["equipment"])}</p>
        <p class="price">{r["price_per_hour"]} ₽/час</p>
        <a class="btn btn-green" href="/room?id={r["id"]}">Выбрать время →</a>
      </div></div>"""


def page_building(bld_id, qs):
    db = get_db()
    bld = db.execute("""
        SELECT b.*, c.name AS city_name FROM buildings b
        JOIN cities c ON c.id=b.city_id WHERE b.id=?""", (bld_id,)).fetchone()
    if not bld:
        db.close()
        return page("Ошибка", "<h1>Здание не найдено</h1>")
    rooms = db.execute(
        "SELECT * FROM rooms WHERE building_id=? ORDER BY name", (bld_id,)).fetchall()
    cards = "".join(room_card(r, bld["name"], bld["city_name"]) for r in rooms)
    db.close()
    body = f"""<p class="crumbs"><a href="/">Города</a> / <a href="/city?id={bld["city_id"]}">{esc(bld["city_name"])}</a> / {esc(bld["name"])}</p>
<h1>{esc(bld["name"])}</h1>
<p class="muted">📍 {esc(bld["address"])}</p>
<h2>Свободные помещения</h2>
<div class="stack">{cards}</div>"""
    return page(bld["name"], body)


def page_room(room_id, qs):
    db = get_db()
    r = db.execute("""
        SELECT r.*, b.name AS bname, b.address, b.city_id, c.name AS city_name
        FROM rooms r JOIN buildings b ON b.id=r.building_id
        JOIN cities c ON c.id=b.city_id WHERE r.id=?""", (room_id,)).fetchone()
    if not r:
        db.close()
        return page("Ошибка", "<h1>Помещение не найдено</h1>")

    today = date.today()
    head = "".join(f"<th>{h}:00</th>" for h in range(WORK_START, WORK_END))
    rows = ""
    for d in range(14):
        day = today + timedelta(days=d)
        day_s = day.isoformat()
        occ = occupied_hours(db, room_id, day_s)
        cells = ""
        for h in range(WORK_START, WORK_END):
            past = (day == today and h <= datetime.now().hour)
            if h in occ:
                cells += '<td class="busy">занято</td>'
            elif past:
                cells += '<td class="past">—</td>'
            else:
                cells += (f'<td class="free"><a href="/book?room={room_id}'
                          f'&date={day_s}&start={h}">+</a></td>')
        rows += (f"<tr><td class='day'>{day.strftime('%d.%m')}<br>"
                 f"<small>{day.strftime('%a')}</small></td>{cells}</tr>")

    skud = ('<span class="badge badge-red">Требуется проходка (СКУД) — '
            'при бронировании нужно указать состав посетителей</span>'
            if r["skud_required"] else
            '<span class="badge badge-green">Открытое посещение — проходка не нужна</span>')
    db.close()
    body = f"""<p class="crumbs"><a href="/">Города</a> / <a href="/city?id={r["city_id"]}">{esc(r["city_name"])}</a> / <a href="/building?id={r["building_id"]}">{esc(r["bname"])}</a> / {esc(r["name"])}</p>
<h1>{esc(r["name"])}</h1>
<div class="room-detail">
  <img src="/static/img/{esc(r["photo"])}" alt="Фото помещения (заглушка)">
  <div>
    <p class="room-meta">▦ {r["area"]:g} м² · 👥 до {r["capacity"]} чел. · {skud}</p>
    <p><b>Адрес:</b> {esc(r["address"])}</p>
    <p><b>Тех. характеристики:</b> {esc(r["tech_characteristics"])}</p>
    <p><b>Оборудование:</b> {esc(r["equipment"])}</p>
    <p class="price">{r["price_per_hour"]} ₽/час</p>
  </div>
</div>
<h2>Доступность (ближайшие 14 дней)</h2>
<p class="muted">Нажмите «+» на свободном слоте, чтобы забронировать. Рабочие часы: {WORK_START}:00–{WORK_END}:00.</p>
<table class="calendar"><thead><tr><th>Дата</th>{head}</tr></thead><tbody>{rows}</tbody></table>"""
    return page(r["name"], body)


def page_book_form(room_id, qs):
    db = get_db()
    r = db.execute("""
        SELECT r.*, b.name AS bname, b.address, c.name AS city_name
        FROM rooms r JOIN buildings b ON b.id=r.building_id
        JOIN cities c ON c.id=b.city_id WHERE r.id=?""", (room_id,)).fetchone()
    if not r:
        db.close()
        return page("Ошибка", "<h1>Помещение не найдено</h1>")
    day = qs.get("date", [""])[0]
    start_s = qs.get("start", [""])[0]
    err = qs.get("err", [""])[0]
    db.close()

    try:
        datetime.strptime(day, "%Y-%m-%d")
        start = int(start_s)
        assert WORK_START <= start < WORK_END
    except (ValueError, AssertionError):
        return page("Ошибка", "<h1>Некорректные дата/время. "
                    "Выберите слот в <a href='/'>календаре</a>.</h1>")

    err_div = ""
    if err:
        err_div = '<div class="flash flash-err">✖ ' + esc(err) + '</div>'

    price = r["price_per_hour"]
    if r["skud_required"]:
        skud_block = '''
  <fieldset class="participants">
    <legend>Состав посетителей (обязательно — требуется проходка СКУД)</legend>
    <p class="muted">Укажите каждого, кто будет посещать помещение. Данные нужны для оформления пропусков.</p>
    <div id="plist"></div>
    <button type="button" class="btn btn-light" onclick="addParticipant()">+ Добавить посетителя</button>
  </fieldset>
  <script>
  let n = 0;
  function addParticipant() {
    n++;
    const div = document.createElement('div');
    div.className = 'p-row';
    div.innerHTML =
      '<input name="p_name_' + n + '" placeholder="ФИО полностью *" required>' +
      '<input name="p_ser_' + n + '" placeholder="Серия паспорта *" size="6" required>' +
      '<input name="p_num_' + n + '" placeholder="Номер паспорта *" size="8" required>' +
      '<input name="p_pos_' + n + '" placeholder="Должность">' +
      '<button type="button" class="btn btn-del" onclick="this.parentNode.remove()">✕</button>';
    document.getElementById('plist').appendChild(div);
  }
  addParticipant();
  function updCost() {
    const d = +document.getElementById('dur').value;
    document.getElementById('cost').textContent = (d * PRICE) + ' ₽';
  }
  </script>'''.replace("PRICE", str(price))
    else:
        skud_block = ('<script>function updCost(){var d=+document.getElementById("dur").value;'
                      'document.getElementById("cost").textContent=(d*' + str(price) + ')+" ₽";}</script>')

    dur_opts = "".join('<option value="' + str(d) + '"' + (" selected" if d == 2 else "") + '>' + str(d) + ' ч.</option>'
                       for d in (1, 2, 3, 4))
    body = f"""<p class="crumbs"><a href="/room?id={room_id}">← Назад к календарю</a></p>
<h1>Бронирование: {esc(r["name"])}</h1>
<p class="muted">{esc(r["city_name"])} · {esc(r["bname"])} · {esc(r["address"])} · <span class="price">{price} ₽/час</span></p>
{err_div}
<div class="card form-card">
<form method="post" action="/book">
  <input type="hidden" name="room_id" value="{room_id}">
  <h3>Дата и время</h3>
  <div class="form-row">
    <label>Дата<br><input type="date" name="date" value="{day}" readonly></label>
    <label>Начало<br><input type="text" name="start_time" value="{start}:00" readonly></label>
    <label>Длительность<br>
      <select name="duration" id="dur" onchange="updCost()">{dur_opts}</select></label>
    <label>Стоимость<br><span class="price" id="cost">{2 * price} ₽</span></label>
  </div>
  <h3>Данные юридического лица</h3>
  <div class="form-row">
    <label>Название организации *<br><input name="company_name" required placeholder="ООО «Название»"></label>
    <label>Контактное лицо *<br><input name="contact_name" required placeholder="Иванов И.И."></label>
  </div>
  <div class="form-row">
    <label>Телефон *<br><input name="contact_phone" required placeholder="+7 (___) ___-__-__"></label>
    <label>E-mail<br><input type="email" name="contact_email" placeholder="office@company.ru"></label>
  </div>
  <label>Комментарий<br><input name="comment" placeholder="Необязательно"></label>
  {skud_block}
  <button type="submit" class="btn btn-green btn-big">Забронировать →</button>
</form>
</div>"""
    return page("Бронирование", body)


def page_bookings(qs):
    db = get_db()
    rows = db.execute("""
        SELECT b.*, r.name AS rname, r.price_per_hour, bd.name AS bname,
               c.name AS city_name,
               (SELECT COUNT(*) FROM participants p WHERE p.booking_id=b.id) AS n_p
        FROM bookings b
        JOIN rooms r ON r.id=b.room_id
        JOIN buildings bd ON bd.id=r.building_id
        JOIN cities c ON c.id=bd.city_id
        ORDER BY b.date DESC, b.start_time DESC""").fetchall()
    trs = ""
    for b in rows:
        status = ("<span class='badge badge-green'>активна</span>"
                  if b["status"] == "active" else
                  "<span class='badge badge-gray'>отменена</span>")
        cost = (b["end_time"] - b["start_time"]) * b["price_per_hour"]
        cancel = ""
        if b["status"] == "active":
            cancel = (f'''<form method="post" action="/cancel" style="display:inline" onsubmit="return confirm('Отменить бронирование?')">'''
                      f'''<input type="hidden" name="id" value="{b['id']}"><button class="btn btn-del">Отменить</button></form>''')

        trs += f"""<tr>
          <td>#{b["id"]}</td>
          <td>{b["date"]}<br>{b["start_time"]}:00–{b["end_time"]}:00</td>
          <td>{esc(b["rname"])}<br><small class="muted">{esc(b["bname"])}, {esc(b["city_name"])}</small></td>
          <td>{esc(b["company_name"])}<br><small class="muted">{esc(b["contact_name"])} · {esc(b["contact_phone"])}</small></td>
          <td>{b["n_p"]} чел.</td>
          <td>{cost} ₽</td><td>{status}</td><td>{cancel}</td></tr>"""
    db.close()
    empty = '<tr><td colspan="8">Бронирований пока нет.</td></tr>'
    body = f"""<h1>Все бронирования</h1>
<p class="muted">Эта таблица и есть таблица записей из БД — занятые слоты в календарях считаются по ней.</p>
{flashes(qs)}
<table class="table">
  <thead><tr><th>№</th><th>Дата, время</th><th>Помещение</th><th>Юрлицо / контакт</th><th>Посетители</th><th>Сумма</th><th>Статус</th><th></th></tr></thead>
  <tbody>{trs if trs else empty}</tbody>
</table>"""
    return page("Бронирования", body)


# ---------------------------------------------------------- обработка ----
def do_book(data):
    db = get_db()
    try:
        room_id = int(data.get("room_id", ["0"])[0])
        day = data.get("date", [""])[0]
        start = int(data.get("start_time", ["0"])[0].split(":")[0])
        duration = int(data.get("duration", ["2"])[0])
    except (ValueError, IndexError):
        db.close()
        return "/?err=Некорректные+данные+формы"

    end = start + duration
    company = data.get("company_name", [""])[0].strip()
    contact = data.get("contact_name", [""])[0].strip()
    phone = data.get("contact_phone", [""])[0].strip()

    def back(msg):
        db.close()
        return (f"/book?room={room_id}&date={day}&start={start}"
                f"&err={msg.replace(' ', '+')}")

    r = db.execute("SELECT * FROM rooms WHERE id=?", (room_id,)).fetchone()
    if not r:
        db.close()
        return "/?err=Помещение+не+найдено"
    try:
        if date.fromisoformat(day) < date.today():
            return back("Нельзя бронировать прошедшую дату")
    except ValueError:
        return back("Некорректная дата")
    if not (WORK_START <= start and end <= WORK_END and duration >= 1):
        return back("Некорректное время (рабочие часы 09:00–20:00)")
    if not company or not contact or not phone:
        return back("Заполните обязательные поля: организация, контакт, телефон")
    if has_overlap(db, room_id, day, start, end):
        return back("Этот слот уже занят, выберите другое время")

    participants = []
    i = 1
    while f"p_name_{i}" in data:
        nm = data[f"p_name_{i}"][0].strip()
        if nm:
            participants.append((
                nm,
                data.get(f"p_ser_{i}", [""])[0].strip(),
                data.get(f"p_num_{i}", [""])[0].strip(),
                data.get(f"p_pos_{i}", [""])[0].strip()))
        i += 1
    if r["skud_required"] and not participants:
        return back("Для помещения с СКУД укажите хотя бы одного посетителя")
    for p in participants:
        if not (p[0] and p[1] and p[2]):
            return back("У каждого посетителя заполните ФИО и паспорт")

    cur = db.execute("""
        INSERT INTO bookings (room_id, date, start_time, end_time,
                company_name, contact_name, contact_phone, contact_email,
                comment, status, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,'active',?)""", (
        room_id, day, start, end, company, contact, phone,
        data.get("contact_email", [""])[0].strip(),
        data.get("comment", [""])[0].strip(),
        datetime.now().isoformat(timespec="seconds")))
    bid = cur.lastrowid
    for p in participants:
        db.execute("""
            INSERT INTO participants (booking_id, full_name, passport_ser,
                                      passport_num, position)
            VALUES (?,?,?,?,?)""", (bid,) + p)
    db.commit()
    db.close()
    return f"/bookings?ok=Бронь №{bid} создана: {day} {start}:00–{end}:00"


CONTENT_TYPES = {".css": "text/css; charset=utf-8",
                 ".png": "image/png", ".jpg": "image/jpeg"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send(self, body, ctype="text/html; charset=utf-8", code=200):
        b = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _redirect(self, location):
        from urllib.parse import quote
        self.send_response(303)
        self.send_header("Location", quote(location, safe="/:?=&+%"))
        self.end_headers()

    def do_GET(self):
        u = urlparse(self.path)
        path, qs = u.path, parse_qs(u.query)

        if path.startswith("/static/"):
            fp = os.path.join(STATIC_DIR, path[len("/static/"):])
            if os.path.isfile(fp):
                ext = os.path.splitext(fp)[1]
                with open(fp, "rb") as f:
                    self._send(f.read(), CONTENT_TYPES.get(ext, "application/octet-stream"))
            else:
                self._send("not found", code=404)
            return

        routes = {
            "/": lambda: page_cities(qs),
            "/city": lambda: page_city(int(qs.get("id", ["0"])[0]), qs),
            "/building": lambda: page_building(int(qs.get("id", ["0"])[0]), qs),
            "/room": lambda: page_room(int(qs.get("id", ["0"])[0]), qs),
            "/book": lambda: page_book_form(int(qs.get("room", ["0"])[0]), qs),
            "/bookings": lambda: page_bookings(qs),
        }
        try:
            handler = routes.get(path)
            self._send(handler() if handler else page("404", "<h1>Страница не найдена</h1>"))
        except Exception as e:
            self._send(page("Ошибка", f"<h1>Ошибка</h1><p>{esc(e)}</p>"))

    def do_POST(self):
        u = urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0))
        data = parse_qs(self.rfile.read(length).decode("utf-8"))

        if u.path == "/book":
            self._redirect(do_book(data))
        elif u.path == "/cancel":
            db = get_db()
            db.execute("UPDATE bookings SET status='cancelled' WHERE id=?", (int(data.get("id", ["0"])[0]),))
            db.commit()
            db.close()
            self._redirect("/bookings?ok=Бронирование отменено, слот освобождён")
        else:
            self._redirect("/")


def run(host="127.0.0.1", port=5000):
    init_db()
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"Прототип запущен: http://{host}:{port}  (Ctrl+C — остановить)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
