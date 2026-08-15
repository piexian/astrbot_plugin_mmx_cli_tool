from __future__ import annotations

import base64
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_mmx_cli_tool.mmx.utils import (  # noqa: E402
    get_shared_temp_dir,
    resolve_data_path,
    resolve_image,
    resolve_local_input_path,
    resolve_subject_reference,
)


class PathSafetyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.data_dir = self.root / "plugin_data"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.image = self.data_dir / "image.jpg"
        self.image.write_bytes(b"image-bytes")
        self.secret = self.root / "secret.txt"
        self.secret.write_text("secret", encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_resolve_data_path_accepts_relative_file_inside_data_dir(self) -> None:
        self.assertEqual(
            self.image.resolve(),
            resolve_data_path(str(self.data_dir), "image.jpg"),
        )

    def test_resolve_data_path_accepts_file_uri_inside_data_dir(self) -> None:
        self.assertEqual(
            self.image.resolve(),
            resolve_data_path(str(self.data_dir), self.image.as_uri()),
        )

    def test_resolve_data_path_rejects_traversal_and_outside_absolute(self) -> None:
        self.assertIsNone(resolve_data_path(str(self.data_dir), "../secret.txt"))
        self.assertIsNone(resolve_data_path(str(self.data_dir), str(self.secret)))
        self.assertIsNone(resolve_data_path(str(self.data_dir), self.secret.as_uri()))

    def test_untrusted_local_input_requires_data_dir(self) -> None:
        with self.assertRaises(ValueError):
            resolve_local_input_path(str(self.secret))

    def test_local_input_rejects_outside_data_dir(self) -> None:
        with self.assertRaises(ValueError):
            resolve_local_input_path("../secret.txt", data_dir=str(self.data_dir))

    def test_trusted_local_input_allows_attachment_path(self) -> None:
        self.assertEqual(
            self.secret.resolve(),
            resolve_local_input_path(
                str(self.secret),
                allow_trusted_local_path=True,
            ),
        )

    def test_shared_temp_dir_exists(self) -> None:
        temp_dir = get_shared_temp_dir()
        self.assertTrue(Path(temp_dir).is_dir())

    def test_local_input_allows_extra_allowed_dir(self) -> None:
        extra = self.root / "astrbot_temp"
        extra.mkdir()
        temp_image = extra / "photo.jpg"
        temp_image.write_bytes(b"temp-image")
        self.assertEqual(
            temp_image.resolve(),
            resolve_local_input_path(
                str(temp_image),
                data_dir=str(self.data_dir),
                extra_allowed_dirs=[str(extra)],
            ),
        )

    def test_local_input_relative_path_resolved_via_extra_allowed_dir(self) -> None:
        extra = self.root / "astrbot_temp"
        extra.mkdir()
        temp_image = extra / "photo.jpg"
        temp_image.write_bytes(b"temp-image")

        # 相对路径逃出 data_dir 但落入 extra_allowed_dirs 时仍可解析
        self.assertEqual(
            temp_image.resolve(),
            resolve_local_input_path(
                "../astrbot_temp/photo.jpg",
                data_dir=str(self.data_dir),
                extra_allowed_dirs=[str(extra)],
            ),
        )

        # 同时逃出 data_dir 与 extra_allowed_dirs 的 .. 穿越仍被拒绝
        with self.assertRaises(ValueError):
            resolve_local_input_path(
                "../secret.txt",
                data_dir=str(self.data_dir),
                extra_allowed_dirs=[str(extra)],
            )

    def test_bare_filename_falls_back_to_extra_allowed_dir(self) -> None:
        extra = self.root / "astrbot_temp"
        extra.mkdir()
        temp_image = extra / "photo.jpg"
        temp_image.write_bytes(b"temp-image")

        # data_dir 内不存在同名文件时，裸文件名回退到 extra_allowed_dirs
        self.assertEqual(
            temp_image.resolve(),
            resolve_local_input_path(
                "photo.jpg",
                data_dir=str(self.data_dir),
                extra_allowed_dirs=[str(extra)],
            ),
        )

    def test_bare_filename_prefers_data_dir_when_both_exist(self) -> None:
        extra = self.root / "astrbot_temp"
        extra.mkdir()
        (extra / self.image.name).write_bytes(b"temp-image")

        # 两个目录都有同名文件时优先 data_dir 内的文件
        self.assertEqual(
            self.image.resolve(),
            resolve_local_input_path(
                self.image.name,
                data_dir=str(self.data_dir),
                extra_allowed_dirs=[str(extra)],
            ),
        )

    def test_local_input_rejects_outside_data_and_extra_dirs(self) -> None:
        extra = self.root / "astrbot_temp"
        extra.mkdir()
        with self.assertRaises(ValueError):
            resolve_local_input_path(
                str(self.secret),
                data_dir=str(self.data_dir),
                extra_allowed_dirs=[str(extra)],
            )

    async def test_resolve_image_encodes_extra_allowed_dir_file(self) -> None:
        extra = self.root / "astrbot_temp"
        extra.mkdir()
        temp_image = extra / "photo.jpg"
        temp_image.write_bytes(b"temp-image")

        data_uri = await resolve_image(
            str(temp_image),
            data_dir=str(self.data_dir),
            extra_allowed_dirs=[str(extra)],
        )

        prefix, encoded = data_uri.split(",", 1)
        self.assertEqual("data:image/jpeg;base64", prefix)
        self.assertEqual(b"temp-image", base64.b64decode(encoded))

    async def test_resolve_image_encodes_data_dir_file(self) -> None:
        data_uri = await resolve_image("image.jpg", data_dir=str(self.data_dir))

        prefix, encoded = data_uri.split(",", 1)
        self.assertEqual("data:image/jpeg;base64", prefix)
        self.assertEqual(b"image-bytes", base64.b64decode(encoded))

    async def test_resolve_image_rejects_untrusted_outside_file(self) -> None:
        with self.assertRaises(ValueError):
            await resolve_image(str(self.secret), data_dir=str(self.data_dir))

    async def test_subject_reference_rejects_outside_image_param(self) -> None:
        with self.assertRaises(ValueError):
            await resolve_subject_reference(
                f"type=character,image={self.secret}",
                data_dir=str(self.data_dir),
            )


if __name__ == "__main__":
    unittest.main()
