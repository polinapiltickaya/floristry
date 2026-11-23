import sqlite3
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import parse_qs, urlparse
from flask import Flask, abort, render_template, request

from build_db import DB_PATH, init_db

app = Flask(__name__)
app.secret_key = "floristry-kb-secret"


def get_youtube_thumbnail(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    video_id = None
    if "youtu.be" in host:
        video_id = parsed.path.lstrip("/").split("/")[0]
    elif "youtube.com" in host:
        if parsed.path == "/watch":
            video_id = parse_qs(parsed.query).get("v", [None])[0]
        elif parsed.path.startswith(("/embed/", "/shorts/", "/v/")):
            parts = parsed.path.split("/")
            if len(parts) > 2:
                video_id = parts[2]
    if video_id:
        return f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg"
    return ""


def get_instagram_handle(url: str) -> str:
    if not url:
        return "@instagram"
    parsed = urlparse(url.strip())
    path = parsed.path.strip("/")
    if path:
        handle = path.split("/")[0]
        return f"@{handle}"
    return "@instagram"


app.jinja_env.globals["youtube_thumb"] = get_youtube_thumbnail
app.jinja_env.globals["instagram_handle"] = get_instagram_handle


def ensure_db_ready() -> None:
    need_init = False
    if not DB_PATH.exists():
        need_init = True
    else:
        try:
            conn = sqlite3.connect(DB_PATH)
            count = conn.execute("SELECT COUNT(*) FROM plants").fetchone()[0]
            styles_count = conn.execute("SELECT COUNT(*) FROM styles").fetchone()[0]
            conn.close()
            if count == 0 or styles_count == 0:
                need_init = True
        except Exception:
            need_init = True
    if need_init:
        init_db(DB_PATH)


def get_db() -> sqlite3.Connection:
    ensure_db_ready()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn





def search_all_kb(conn: sqlite3.Connection, query: str) -> Dict[str, List[Dict[str, Any]]]:

    import re

    results = {
        "plants": [],
        "bouquets": [],
        "styles": [],
        "occasions": [],
        "shops": [],
        "resources": []
    }

    q_low = (query or "").strip().lower().replace("ё", "е")

    if not q_low:
        return results

    stop_words = {
        "из", "для", "с", "со", "на", "в", "во",
        "по", "и", "или", "о", "об", "от", "к", "за"
    }

    section_aliases = {
        "plants": (
            "растен", "цвет", "зелен", "сухоцвет"
        ),
        "bouquets": (
            "букет", "композиц"
        ),
        "styles": (
            "стил", "декоратив", "вегетатив",
            "линейн", "массивн"
        ),
        "occasions": (
            "повод", "юбиле", "праздник"
        ),
        "shops": (
            "магазин", "салон", "мастерск", "студи"
        ),
        "resources": (
            "материал", "видео", "стат",
            "урок", "мастер-класс"
        )
    }


    occasion_name_hints = (
        "свадеб", "свадьб", "день рождения",
        "8 марта", "юбиле", "свидан", "комплимент",
        "интерьер"
    )

    section_priority = [
        "resources",
        "shops",
        "styles",
        "bouquets",
        "plants",
        "occasions"
    ]


    def normalize_word(word):

        word = word.lower().replace("ё", "е")

        endings = (
            "иями", "ями", "ами",
            "ого", "ему", "ому",
            "ыми", "ими", "ых", "их",
            "ее", "ие", "ые", "ое",
            "ая", "яя", "ий", "ый", "ой",
            "ую", "юю", "ей", "ов", "ев",
            "ам", "ям", "ах", "ях",
            "ом", "ем", "ою", "ею",
            "а", "я", "ы", "и", "у", "ю", "е"
        )

        for ending in endings:
            if len(word) > len(ending) + 2 and word.endswith(ending):
                return word[:-len(ending)]

        return word

    def get_words(text):

        words = re.findall(
            r"[а-яёa-z0-9]+",
            str(text or "").lower()
        )

        return [normalize_word(word) for word in words]


    selected_section = None

    for section in section_priority:

        aliases = section_aliases[section]

        if any(alias in q_low for alias in aliases):
            selected_section = section
            break

    terms = []

    for word in re.findall(r"[а-яёa-z0-9]+", q_low):

        if word in stop_words:
            continue

        is_section_word = False

        for aliases in section_aliases.values():
            if any(word.startswith(alias) for alias in aliases):
                is_section_word = True
                break

        if not is_section_word:
            terms.append(normalize_word(word))


    matching_plant_ids = []

    if terms:

        plant_rows = conn.execute(
            "SELECT plant_id, name_ru FROM plants"
        ).fetchall()

        for plant in plant_rows:

            plant_words = get_words(plant["name_ru"])

            if all(term in plant_words for term in terms):
                matching_plant_ids.append(plant["plant_id"])


    matching_occasion_ids = []

    occasion_rows_all = conn.execute(
        "SELECT occasion_id, name FROM occasions"
    ).fetchall()

    for occ in occasion_rows_all:

        occ_words = get_words(occ["name"])

        if terms and all(term in occ_words for term in terms):
            matching_occasion_ids.append(occ["occasion_id"])
            continue

        if any(hint in q_low for hint in occasion_name_hints):
            if any(w in q_low for w in occ_words if len(w) > 2):
                matching_occasion_ids.append(occ["occasion_id"])

    matching_occasion_ids = list(dict.fromkeys(matching_occasion_ids))

    section_only_query = (
        selected_section is not None and len(terms) == 0
    )


    def matches_terms(*fields):

        if not terms:
            return section_only_query

        content_words = []

        for field in fields:
            content_words.extend(get_words(field))

        for term in terms:
            if term not in content_words:
                return False

        return True

    def section_allowed(section):

        return (
            selected_section is None
            or selected_section == section
        )


    if section_allowed("plants"):

        plant_rows = conn.execute(
            "SELECT * FROM plants ORDER BY plant_id ASC"
        ).fetchall()

        found_plant_ids = set()

        for row in plant_rows:

            p = dict(row)
            matched = False

            # Обычный текстовый поиск
            if matches_terms(
                p.get("name_ru"),
                p.get("name_latin"),
                p.get("category"),
                p.get("color"),
                p.get("season"),
                p.get("description"),
                p.get("care_info")
            ):
                matched = True

            if not matched and matching_occasion_ids:

                link = conn.execute(
                    """
                    SELECT 1 FROM plant_occasions
                    WHERE plant_id = ? AND occasion_id IN ({})
                    LIMIT 1
                    """.format(
                        ",".join("?" * len(matching_occasion_ids))
                    ),
                    (p["plant_id"], *matching_occasion_ids)
                ).fetchone()

                if link:
                    matched = True

            if matched:

                found_plant_ids.add(p["plant_id"])

                p["occasions"] = [
                    r["name"]
                    for r in conn.execute(
                        """
                        SELECT o.name
                        FROM plant_occasions po
                        JOIN occasions o
                            ON po.occasion_id = o.occasion_id
                        WHERE po.plant_id = ?
                        ORDER BY o.occasion_id
                        """,
                        (p["plant_id"],)
                    ).fetchall()
                ]

                p["bouquets"] = [
                    r["name"]
                    for r in conn.execute(
                        """
                        SELECT b.name
                        FROM bouquet_composition bc
                        JOIN bouquets b
                            ON bc.bouquet_id = b.bouquet_id
                        WHERE bc.plant_id = ?
                        ORDER BY b.bouquet_id
                        """,
                        (p["plant_id"],)
                    ).fetchall()
                ]

                results["plants"].append(p)


    if section_allowed("bouquets"):

        bouquet_rows = conn.execute(
            "SELECT * FROM bouquets ORDER BY bouquet_id ASC"
        ).fetchall()

        for row in bouquet_rows:

            b = dict(row)

            comp = [
                dict(c)
                for c in conn.execute(
                    """
                    SELECT
                        bc.quantity,
                        bc.role,
                        p.plant_id,
                        p.name_ru,
                        p.name_latin,
                        p.color,
                        p.category,
                        p.season
                    FROM bouquet_composition bc
                    JOIN plants p
                        ON bc.plant_id = p.plant_id
                    WHERE bc.bouquet_id = ?
                    ORDER BY p.plant_id
                    """,
                    (b["bouquet_id"],)
                ).fetchall()
            ]

            b_occasions = [
                r["name"]
                for r in conn.execute(
                    """
                    SELECT o.name
                    FROM bouquet_occasions bo
                    JOIN occasions o
                        ON bo.occasion_id = o.occasion_id
                    WHERE bo.bouquet_id = ?
                    ORDER BY o.occasion_id
                    """,
                    (b["bouquet_id"],)
                ).fetchall()
            ]

            b_shops = [
                dict(s)
                for s in conn.execute(
                    """
                    SELECT s.name, s.city
                    FROM shop_bouquets sb
                    JOIN shops s
                        ON sb.shop_id = s.shop_id
                    WHERE sb.bouquet_id = ?
                    ORDER BY s.shop_id
                    """,
                    (b["bouquet_id"],)
                ).fetchall()
            ]

            found = False

            # 1. По растению в составе
            if matching_plant_ids:

                bouquet_plant_ids = [c["plant_id"] for c in comp]

                if any(
                    pid in bouquet_plant_ids
                    for pid in matching_plant_ids
                ):
                    found = True

            # 2. По текстовым полям букета
            if not found and terms:

                found = matches_terms(
                    b.get("name"),
                    b.get("description"),
                    b.get("style")
                )

            # 3. По поводу
            if not found and matching_occasion_ids:

                link = conn.execute(
                    """
                    SELECT 1 FROM bouquet_occasions
                    WHERE bouquet_id = ? AND occasion_id IN ({})
                    LIMIT 1
                    """.format(
                        ",".join("?" * len(matching_occasion_ids))
                    ),
                    (b["bouquet_id"], *matching_occasion_ids)
                ).fetchone()

                if link:
                    found = True

            # 4. Только слово «букет»
            if not found and not terms and not matching_plant_ids:
                found = section_only_query

            if found:

                b["composition"] = comp
                b["occasions"] = b_occasions
                b["shops"] = b_shops

                results["bouquets"].append(b)


    if section_allowed("styles"):

        style_rows = conn.execute(
            "SELECT * FROM styles ORDER BY style_id ASC"
        ).fetchall()

        for row in style_rows:

            st = dict(row)

            if matches_terms(
                st.get("name"),
                st.get("subtitle"),
                st.get("description"),
                st.get("characteristics"),
                st.get("usage_info")
            ):
                results["styles"].append(st)


    if section_allowed("occasions") or matching_occasion_ids:

        occasion_rows = conn.execute(
            "SELECT * FROM occasions ORDER BY occasion_id ASC"
        ).fetchall()

        for row in occasion_rows:

            o = dict(row)

            matched = False

            if matches_terms(o.get("name")):
                matched = True

            if not matched and o["occasion_id"] in matching_occasion_ids:
                matched = True

            if matched:

                o["bouquets"] = [
                    r["name"]
                    for r in conn.execute(
                        """
                        SELECT b.name
                        FROM bouquet_occasions bo
                        JOIN bouquets b
                            ON bo.bouquet_id = b.bouquet_id
                        WHERE bo.occasion_id = ?
                        ORDER BY b.bouquet_id
                        """,
                        (o["occasion_id"],)
                    ).fetchall()
                ]

                o["plants"] = [
                    r["name_ru"]
                    for r in conn.execute(
                        """
                        SELECT p.name_ru
                        FROM plant_occasions po
                        JOIN plants p
                            ON po.plant_id = p.plant_id
                        WHERE po.occasion_id = ?
                        ORDER BY p.plant_id
                        """,
                        (o["occasion_id"],)
                    ).fetchall()
                ]

                results["occasions"].append(o)


    if section_allowed("shops"):

        shop_rows = conn.execute(
            "SELECT * FROM shops ORDER BY shop_id ASC"
        ).fetchall()

        for row in shop_rows:

            s = dict(row)

            if matches_terms(
                s.get("name"),
                s.get("city"),
                s.get("description"),
                s.get("instagram_url")
            ):

                s["bouquets"] = [
                    r["name"]
                    for r in conn.execute(
                        """
                        SELECT b.name
                        FROM shop_bouquets sb
                        JOIN bouquets b
                            ON sb.bouquet_id = b.bouquet_id
                        WHERE sb.shop_id = ?
                        ORDER BY b.bouquet_id
                        """,
                        (s["shop_id"],)
                    ).fetchall()
                ]

                results["shops"].append(s)

    # 10. СТАТЬИ И ВИДЕО

    if section_allowed("resources"):

        resource_rows = conn.execute(
            "SELECT * FROM resources ORDER BY resource_id ASC"
        ).fetchall()

        for row in resource_rows:

            r = dict(row)

            if matches_terms(
                r.get("type"),
                r.get("title"),
                r.get("topic")
            ):
                results["resources"].append(r)

    return results




@app.route("/", methods=["GET", "POST"])
def index():
    q = (request.values.get("q") or "").strip()
    search_results = None
    total_matches = 0

    if q:
        conn = get_db()
        try:
            search_results = search_all_kb(conn, q)
            total_matches = sum(len(v) for v in search_results.values())
        finally:
            conn.close()

    return render_template(
        "index.html",
        q=q,
        search_results=search_results,
        total_matches=total_matches,
    )


@app.route("/plants")
def plants_list():
    conn = get_db()
    try:
        plants = conn.execute("SELECT * FROM plants ORDER BY plant_id ASC").fetchall()
        return render_template("plants.html", plants=plants)
    finally:
        conn.close()


@app.route("/plants/<int:plant_id>")
def plant_detail(plant_id: int):
    conn = get_db()
    try:
        plant = conn.execute("SELECT * FROM plants WHERE plant_id = ?", (plant_id,)).fetchone()
        if not plant:
            abort(404)
        occasions = conn.execute(
            """
            SELECT o.name FROM plant_occasions po
            JOIN occasions o ON o.occasion_id = po.occasion_id
            WHERE po.plant_id = ?
            """,
            (plant_id,),
        ).fetchall()
        bouquets = conn.execute(
            """
            SELECT b.bouquet_id, b.name, b.style, bc.quantity, bc.role
            FROM bouquet_composition bc
            JOIN bouquets b ON b.bouquet_id = bc.bouquet_id
            WHERE bc.plant_id = ?
            """,
            (plant_id,),
        ).fetchall()
        return render_template(
            "plant_detail.html",
            plant=plant,
            occasions=[r["name"] for r in occasions],
            bouquets=bouquets,
        )
    finally:
        conn.close()


@app.route("/bouquets")
def bouquets_list():
    conn = get_db()
    try:
        bouquets = conn.execute("SELECT * FROM bouquets ORDER BY bouquet_id ASC").fetchall()
        return render_template("bouquets.html", bouquets=bouquets)
    finally:
        conn.close()


@app.route("/bouquets/<int:bouquet_id>")
def bouquet_detail(bouquet_id: int):
    conn = get_db()
    try:
        bouquet = conn.execute("SELECT * FROM bouquets WHERE bouquet_id = ?", (bouquet_id,)).fetchone()
        if not bouquet:
            abort(404)
        composition = conn.execute(
            """
            SELECT bc.quantity, bc.role, p.plant_id, p.name_ru, p.color, p.season, p.care_info
            FROM bouquet_composition bc
            JOIN plants p ON p.plant_id = bc.plant_id
            WHERE bc.bouquet_id = ?
            """,
            (bouquet_id,),
        ).fetchall()
        occasions = conn.execute(
            """
            SELECT o.name FROM bouquet_occasions bo
            JOIN occasions o ON o.occasion_id = bo.occasion_id
            WHERE bo.bouquet_id = ?
            """,
            (bouquet_id,),
        ).fetchall()
        shops = conn.execute(
            """
            SELECT s.shop_id, s.name, s.city, s.instagram_url, s.description
            FROM shop_bouquets sb
            JOIN shops s ON s.shop_id = sb.shop_id
            WHERE sb.bouquet_id = ?
            """,
            (bouquet_id,),
        ).fetchall()
        return render_template(
            "bouquet_detail.html",
            bouquet=bouquet,
            composition=composition,
            occasions=[r["name"] for r in occasions],
            shops=shops,
        )
    finally:
        conn.close()


@app.route("/styles")
def styles_list():
    conn = get_db()
    try:
        styles = conn.execute("SELECT * FROM styles ORDER BY style_id ASC").fetchall()
        return render_template("styles.html", styles=styles)
    finally:
        conn.close()


@app.route("/styles/<int:style_id>")
def style_detail(style_id: int):
    conn = get_db()
    try:
        style = conn.execute("SELECT * FROM styles WHERE style_id = ?", (style_id,)).fetchone()
        if not style:
            abort(404)
        features = [item.strip() for item in (style["characteristics"] or "").split(";") if item.strip()]
        return render_template("style_detail.html", style=style, features=features)
    finally:
        conn.close()


@app.route("/materials")
def materials_list():
    conn = get_db()
    try:
        resources = conn.execute("SELECT * FROM resources ORDER BY resource_id ASC").fetchall()
        return render_template("materials.html", resources=resources)
    finally:
        conn.close()


@app.route("/shops")
def shops_list():
    conn = get_db()
    try:
        shops = conn.execute("SELECT * FROM shops ORDER BY shop_id ASC").fetchall()
        shops_data = []
        for s in shops:
            bq = conn.execute(
                """
                SELECT b.bouquet_id, b.name FROM shop_bouquets sb
                JOIN bouquets b ON b.bouquet_id = sb.bouquet_id
                WHERE sb.shop_id = ?
                """,
                (s["shop_id"],),
            ).fetchall()
            shops_data.append({"info": s, "bouquets": bq})
        return render_template("shops.html", shops=shops_data)
    finally:
        conn.close()


if __name__ == "__main__":
    ensure_db_ready()
    app.run(debug=False, host="127.0.0.1", port=5000)
