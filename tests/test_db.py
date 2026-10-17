from bot.db import Database, parse_tags


def make_db():
    db = Database(":memory:")
    db.add_user(1, "Аня", is_admin=True)
    return db


def test_parse_tags():
    assert parse_tags("Суп, #Ужин #на  скорую руку,суп") == ["суп", "ужин", "на скорую руку"]
    assert parse_tags(" , # ") == []


def test_recipe_roundtrip_and_tags():
    db = make_db()
    rid = db.add_recipe(
        "own", "Борщ", 1, "Аня", ingredients="Свёкла\nКапуста", tags=["Суп", "обед"], photos=["p1", "p2"]
    )
    r = db.get_recipe(rid)
    assert r.title == "Борщ" and r.tags == ["обед", "суп"] and r.photos == ["p1", "p2"]
    db.add_photos(rid, ["p3"])
    assert db.get_recipe(rid).photos == ["p1", "p2", "p3"]
    db.replace_photos(rid, ["x"])
    assert db.get_recipe(rid).photos == ["x"]
    db.set_tags(rid, ["суп"])
    assert [name for _, name, _ in db.list_tags()] == ["суп"]  # неиспользуемый тег удалён


def test_search_is_case_insensitive_for_cyrillic_and_yo():
    db = make_db()
    rid = db.add_recipe("own", "Борщ", 1, "Аня", ingredients="Свёкла", tags=["суп"])
    db.add_recipe("found", "Блины", 1, "Аня", notes="на кефире 50%")
    assert [b.id for b in db.search("СВЕКЛА")[0]] == [rid]
    assert [b.id for b in db.search("борщ суп")[0]] == [rid]
    assert db.search("борщ кефир")[1] == 0
    assert db.search("50%")[1] == 1
    assert db.search("%")[1] == 1  # % экранируется, а не совпадает со всем
    db.update_field(rid, "title", "Щи")
    assert db.search("борщ")[1] == 0 and db.search("щи")[1] == 1


def test_list_and_pagination():
    db = make_db()
    ids = [db.add_recipe("own", f"Блюдо {i}", 1, "Аня", tags=["t"]) for i in range(5)]
    db.add_recipe("found", "Чужое", 1, "Аня")
    items, total = db.list_recipes(kind="own", offset=0, limit=2)
    assert total == 5 and [i.id for i in items] == ids[::-1][:2]
    tag_id = db.list_tags()[0][0]
    assert db.list_recipes(tag_id=tag_id)[1] == 5
    db.set_kind(ids[0], "found")
    assert db.stats() == {"own": 4, "found": 2}


def test_invites_are_single_use():
    db = make_db()
    code = db.create_invite(1)
    assert db.use_invite(code.lower(), 2, "Боря")
    assert db.get_user(2).name == "Боря"
    assert not db.use_invite(code, 3, "Вика")
    assert db.get_user(3) is None
    assert not db.remove_user(1)  # админа не удалить
    assert db.remove_user(2)


def test_delete_cascades():
    db = make_db()
    rid = db.add_recipe("own", "Борщ", 1, "Аня", tags=["суп"], photos=["p"])
    db.delete_recipe(rid)
    assert db.get_recipe(rid) is None
    assert db.list_tags() == []
    assert db.conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0] == 0
