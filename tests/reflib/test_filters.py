"""Tests for reference library boilerplate and licence filtering."""

from pathlib import Path

import pytest

from kairix.knowledge.reflib.filters import (
    filter_collection,
    is_boilerplate_filename,
    is_boilerplate_path,
    is_translation,
    matches_source_excludes,
    should_include,
)
from kairix.knowledge.reflib.sources import SourceDef


@pytest.mark.unit
class TestBoilerplateFilename:
    @pytest.mark.unit
    def test_contributing_detected(self):
        assert is_boilerplate_filename(Path("repo/CONTRIBUTING.md"))

    @pytest.mark.unit
    def test_code_of_conduct_detected(self):
        assert is_boilerplate_filename(Path("repo/CODE_OF_CONDUCT.md"))

    @pytest.mark.unit
    def test_license_detected(self):
        assert is_boilerplate_filename(Path("repo/LICENSE.md"))

    @pytest.mark.unit
    def test_case_insensitive(self):
        assert is_boilerplate_filename(Path("repo/Contributing.md"))
        assert is_boilerplate_filename(Path("repo/license.txt"))

    @pytest.mark.unit
    def test_normal_content_passes(self):
        assert not is_boilerplate_filename(Path("repo/docs/guide.md"))

    @pytest.mark.unit
    def test_readme_is_not_boilerplate(self):
        assert not is_boilerplate_filename(Path("repo/README.md"))


@pytest.mark.unit
class TestBoilerplatePath:
    @pytest.mark.unit
    def test_github_dir_excluded(self):
        assert is_boilerplate_path(Path("repo/.github/workflows/ci.yml"))

    @pytest.mark.unit
    def test_node_modules_excluded(self):
        assert is_boilerplate_path(Path("repo/node_modules/pkg/readme.md"))

    @pytest.mark.unit
    def test_pycache_excluded(self):
        assert is_boilerplate_path(Path("repo/__pycache__/mod.cpython.pyc"))

    @pytest.mark.unit
    def test_normal_path_passes(self):
        assert not is_boilerplate_path(Path("repo/docs/getting-started.md"))


@pytest.mark.unit
class TestTranslation:
    @pytest.mark.unit
    def test_translations_dir_detected(self):
        assert is_translation(Path("repo/translations/fr/readme.md"))

    @pytest.mark.unit
    def test_i18n_dir_detected(self):
        assert is_translation(Path("repo/i18n/de/guide.md"))

    @pytest.mark.unit
    def test_language_subdir_detected(self):
        assert is_translation(Path("repo/content/zh/intro.md"))

    @pytest.mark.unit
    def test_english_content_passes(self):
        assert not is_translation(Path("repo/docs/guide.md"))


@pytest.mark.unit
class TestSourceExcludes:
    @pytest.mark.unit
    def test_per_source_pattern_matched(self):
        source = SourceDef(
            name="Test",
            collection="test",
            dir_name="test-src",
            licence="MIT",
            licence_tier=2,
            source_url="https://example.com",
            exclude_patterns=("contents/blog/",),
        )
        assert matches_source_excludes(Path("repo/contents/blog/post.md"), source)
        assert not matches_source_excludes(Path("repo/contents/docs/guide.md"), source)

    @pytest.mark.unit
    def test_no_excludes(self):
        source = SourceDef(
            name="Test",
            collection="test",
            dir_name="test-src",
            licence="MIT",
            licence_tier=2,
            source_url="https://example.com",
        )
        assert not matches_source_excludes(Path("any/path.md"), source)


@pytest.mark.unit
class TestShouldInclude:
    @pytest.mark.unit
    def test_normal_file_included(self):
        assert should_include(Path("docs/guide.md"))

    @pytest.mark.unit
    def test_boilerplate_excluded(self):
        assert not should_include(Path("CONTRIBUTING.md"))

    @pytest.mark.unit
    def test_github_path_excluded(self):
        assert not should_include(Path(".github/workflows/ci.yml"))

    @pytest.mark.unit
    def test_translation_excluded(self):
        assert not should_include(Path("translations/fr/guide.md"))


@pytest.mark.unit
class TestFilterCollection:
    @pytest.mark.unit
    def test_filters_boilerplate(self):
        files = [
            Path("docs/guide.md"),
            Path("CONTRIBUTING.md"),
            Path(".github/ci.yml"),
            Path("docs/api.md"),
        ]
        result = filter_collection(files)
        assert len(result) == 2
        assert Path("docs/guide.md") in result
        assert Path("docs/api.md") in result
