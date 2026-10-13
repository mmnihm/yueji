import asyncio
import os
import tempfile
import unittest

os.environ.setdefault("BOT_TOKEN", "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi")
os.environ.setdefault("ADMIN_ID", "123456789")

import bot as app


class YuejiBotSmokeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        app.DB_PATH = os.path.join(self.temp_dir.name, "yueji.db")
        await app.init_db()

    async def asyncTearDown(self):
        self.temp_dir.cleanup()

    async def test_default_admin_settings_are_persisted(self):
        self.assertEqual(await app.get_config("browse_minutes"), "5")
        self.assertEqual(await app.get_config("browse_scope"), "all")
        self.assertEqual(await app.get_config("browse_admin_exempt"), "1")
        self.assertEqual(await app.get_config("random_count"), "3")
        self.assertEqual(await app.get_config("daily_unlock_limit"), "0")
        self.assertEqual(await app.get_config("menu_show_random"), "1")
        self.assertEqual(await app.get_config("menu_order"), "my_info,checkin,random,buy,help")
        self.assertEqual(await app.get_config("announcement_text"), "")

    async def test_config_validation_limits(self):
        value, error = await app.validate_config_value("browse_minutes", "10")
        self.assertEqual(value, "10")
        self.assertIsNone(error)

        value, error = await app.validate_config_value("browse_minutes", "1441")
        self.assertIsNone(value)
        self.assertIsNotNone(error)

        value, error = await app.validate_config_value("buy_options", "10,30,30")
        self.assertIsNone(value)
        self.assertIsNotNone(error)

        value, error = await app.validate_config_value("browse_scope", "videos")
        self.assertEqual(value, "videos")
        self.assertIsNone(error)

        value, error = await app.validate_config_value("daily_unlock_limit", "3")
        self.assertEqual(value, "3")
        self.assertIsNone(error)

        value, error = await app.validate_config_value("daily_unlock_limit", "1001")
        self.assertIsNone(value)
        self.assertIsNotNone(error)

        value, error = await app.validate_config_value("menu_my_info_text", "🎁 每日签到")
        self.assertIsNone(value)
        self.assertIsNotNone(error)

        value, error = await app.validate_config_value("menu_order", "random,my_info,checkin,buy,help")
        self.assertEqual(value, "random,my_info,checkin,buy,help")
        self.assertIsNone(error)

        value, error = await app.validate_config_value("menu_order", "random,random,help")
        self.assertIsNone(value)
        self.assertIsNotNone(error)

    async def test_browse_session_starts_with_expiry_for_regular_user(self):
        session = await app.create_browse_session(987654, "folder-a")
        self.assertIsNotNone(session)
        self.assertIsNotNone(session[0])
        self.assertIsNotNone(session[1])
        self.assertFalse(await app.browse_session_expired(987654, "folder-a"))

    async def test_admin_exemption_creates_unlimited_session(self):
        session = await app.create_browse_session(app.ADMIN_ID, "folder-admin")
        self.assertIsNotNone(session)
        self.assertIsNone(session[1])

    async def test_forum_provider_blocks_destructive_mysql_sync(self):
        await app.set_config("source_provider", "telegram_forum")
        with self.assertRaisesRegex(RuntimeError, "阻止本次旧仓库同步"):
            await app.sync_source_repository()

    async def test_source_resources_gets_grouped_id_column(self):
        import aiosqlite
        async with aiosqlite.connect(app.DB_PATH) as db:
            async with db.execute("PRAGMA table_info(source_resources)") as cur:
                columns = {row[1] for row in await cur.fetchall()}
        self.assertIn("grouped_id", columns)

    async def test_resource_page_does_not_split_album(self):
        rows = [(str(-1001), i, "photo", None) for i in range(1, 10)]
        rows.extend([
            (str(-1001), 10, "photo", "album-a"),
            (str(-1001), 11, "photo", "album-a"),
            (str(-1001), 12, "photo", "album-a"),
            (str(-1001), 13, "photo", None),
        ])
        page1 = app.resource_page(rows, 0)
        self.assertEqual(len(page1), 12)
        self.assertEqual([row[1] for row in page1[-3:]], [10, 11, 12])
        page2 = app.resource_page(rows, len(page1))
        self.assertEqual([row[1] for row in page2], [13])
        self.assertIsNone(page2[0][3])

    async def test_existing_users_table_gets_banned_column(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            legacy_db = os.path.join(temp_dir, "legacy.db")
            import aiosqlite
            async with aiosqlite.connect(legacy_db) as db:
                await db.execute("""
                    CREATE TABLE users (
                        user_id INTEGER PRIMARY KEY,
                        username TEXT,
                        points INTEGER DEFAULT 10,
                        total_spent INTEGER DEFAULT 0,
                        unlocked_count INTEGER DEFAULT 0
                    )
                """)
                await db.commit()
            original_path = app.DB_PATH
            try:
                app.DB_PATH = legacy_db
                await app.init_db()
                async with aiosqlite.connect(legacy_db) as db:
                    async with db.execute("PRAGMA table_info(users)") as cur:
                        columns = {row[1] for row in await cur.fetchall()}
                self.assertIn("banned", columns)
            finally:
                app.DB_PATH = original_path


if __name__ == "__main__":
    unittest.main()
