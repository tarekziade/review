import copy
import mock
import pytest
from distutils.version import LooseVersion

from .conftest import create_temp_fn

from mozphab import environment, exceptions, mozphab
from mozphab.mercurial import Mercurial


mozphab.SHOW_SPINNER = False


@mock.patch("mozphab.mercurial.Mercurial.hg_out")
def test_get_successor(m_hg_hg_out, hg):
    m_hg_hg_out.return_value = []
    assert (None, None) == hg._get_successor("x")

    m_hg_hg_out.return_value = ["1 abcde"]
    assert ["1", "abcde"] == hg._get_successor("x")

    m_hg_hg_out.return_value = ["a", "b"]
    with pytest.raises(exceptions.Error):
        hg._get_successor("x")


@mock.patch("mozphab.mercurial.Mercurial._get_successor")
@mock.patch("mozphab.mercurial.Mercurial.rebase_commit")
@mock.patch("mozphab.mercurial.Mercurial._get_parent")
def test_finalize(m_get_parent, m_hg_rebase, m_hg_get_successor, hg):
    commits = [
        {"rev": "1", "node": "aaa", "orig-node": "aaa"},
        {"rev": "2", "node": "bbb", "orig-node": "bbb"},
        {"rev": "3", "node": "ccc", "orig-node": "ccc"},
    ]

    m_get_parent.return_value = "different:than_others"
    m_hg_get_successor.return_value = (None, None)
    hg.finalize(copy.deepcopy(commits))
    assert m_hg_rebase.call_count == 2
    assert m_hg_rebase.call_args_list == [
        mock.call(
            {"rev": "2", "node": "bbb", "orig-node": "bbb"},
            {"rev": "1", "node": "aaa", "orig-node": "aaa"},
        ),
        mock.call(
            {"rev": "3", "node": "ccc", "orig-node": "ccc"},
            {"rev": "2", "node": "bbb", "orig-node": "bbb"},
        ),
    ]

    m_get_parent.side_effect = ("first", "aaa", "last")
    m_hg_rebase.reset_mock()
    hg.finalize(commits)
    m_hg_rebase.assert_called_once_with(
        {"rev": "3", "node": "ccc", "orig-node": "ccc"},
        {"rev": "2", "node": "bbb", "orig-node": "bbb"},
    )

    m_hg_get_successor.reset_mock()
    m_get_parent.side_effect = None
    m_get_parent.return_value = "different:than_others"
    m_hg_get_successor.side_effect = [(None, None), ("4", "ddd")]
    _commits = commits[:]
    hg.finalize(_commits)
    assert m_hg_get_successor.call_count == 2
    assert m_hg_get_successor.call_args_list == [mock.call("bbb"), mock.call("ccc")]
    assert _commits == [
        {"rev": "1", "node": "aaa", "orig-node": "aaa"},
        {"rev": "2", "node": "bbb", "orig-node": "bbb"},
        {"rev": "3", "node": "ddd", "orig-node": "ccc", "name": "4:ddd"},
    ]

    m_hg_rebase.reset_mock()
    m_hg_get_successor.side_effect = None
    m_hg_get_successor.return_value = (None, None)
    _commits = commits[:]
    _commits[0]["node"] = "AAA"  # node has been amended
    hg.finalize(_commits)
    assert m_hg_rebase.call_count == 2


@mock.patch("mozphab.mercurial.Mercurial.rebase_commit")
def test_finalize_no_evolve(m_hg_rebase, hg):
    hg.use_evolve = False
    hg.finalize([dict(rev="1", node="aaa"), dict(rev="2", node="bbb")])
    assert m_hg_rebase.not_called()


@mock.patch("mozphab.mercurial.parse_config")
@mock.patch("mozphab.mercurial.Mercurial.hg_out")
@mock.patch("mozphab.mercurial.Mercurial.hg_log")
@mock.patch("mozphab.mercurial.hglib.open")
def test_set_args(m_hglib_open, m_hg_hg_log, m_hg_hg_out, m_parse_config, hg):
    class Args:
        def __init__(self, start="(auto)", end=".", safe_mode=False, single=False):
            self.start_rev = start
            self.end_rev = end
            self.safe_mode = safe_mode
            self.single = single

    m_config = mozphab.config
    with pytest.raises(exceptions.Error):
        hg.set_args(Args())

    # baseline config
    hg.mercurial_version = LooseVersion("4.5")
    m_config.safe_mode = False
    m_parse_config.return_value = {"ui.username": "username", "extensions.evolve": ""}

    # evolve & shelve
    hg._hg = []
    hg.set_args(Args())

    assert "extensions.rebase" in hg._config_options
    assert hg._config_options["rebase.experimental.inmemory"] == "true"
    assert hg._extra_options["--pager"] == "never"
    assert hg.use_evolve
    assert not hg.has_shelve

    # inmemory rebase requires hg 4.5+
    hg.mercurial_version = LooseVersion("4.0")
    hg._hg = []
    hg.set_args(Args())
    assert "extensions.rebase" in hg._config_options
    assert "rebase.experimental.inmemory" not in hg._config_options
    assert hg._extra_options["--pager"] == "never"
    hg.mercurial_version = LooseVersion("4.5")

    # safe_mode
    hg._hg = []
    hg.set_args(Args(safe_mode=True))
    options = hg._get_config_options()
    assert options == [
        ("extensions.rebase", ""),
        ("ui.username", "username"),
        ("extensions.evolve", ""),
    ]

    m_config.safe_mode = True
    hg._hg = []
    hg.set_args(Args())
    options = hg._get_config_options()
    assert options == [
        ("extensions.rebase", ""),
        ("ui.username", "username"),
        ("extensions.evolve", ""),
    ]
    m_config.safe_mode = False

    # no evolve
    m_parse_config.return_value = {"ui.username": "username", "extensions.shelve": ""}
    hg._hg = []
    hg.set_args(Args())
    options = hg._get_config_options()
    assert options == [
        ("extensions.rebase", ""),
        ("rebase.experimental.inmemory", "true"),
        ("experimental.evolution.createmarkers", "true"),
        ("extensions.strip", ""),
    ]
    assert not hg.use_evolve
    assert hg.has_shelve

    m_hg_hg_log.side_effect = [("123456789012",), ("098765432109",)]
    hg._hg = []
    hg.set_args(Args())
    assert "123456789012::098765432109" == hg.revset

    m_hg_hg_log.side_effect = IndexError
    with pytest.raises(exceptions.Error):
        hg.set_args(Args())

    m_hg_hg_log.reset_mock()
    m_hg_hg_log.side_effect = [("123456789012",), ("123456789012",)]
    hg.set_args(Args(single=True))
    assert "123456789012" == hg.revset
    assert m_hg_hg_log.call_args_list == [mock.call(".")]

    m_hg_hg_log.reset_mock()
    m_hg_hg_log.side_effect = [("123456789012",), ("123456789012",)]
    hg.set_args(Args(start="start", single=True))
    assert "123456789012" == hg.revset
    assert m_hg_hg_log.call_args_list == [mock.call("start")]


@mock.patch("mozphab.mercurial.Mercurial._status")
def test_clean_worktree(m_status, hg):
    m_status.return_value = {"T": None, "U": None}
    assert hg.is_worktree_clean()

    m_status.return_value = {"T": True, "U": None}
    assert not hg.is_worktree_clean()

    m_status.return_value = {"T": None, "U": True}
    assert hg.is_worktree_clean()

    m_status.return_value = {"T": True, "U": True}
    assert not hg.is_worktree_clean()


@mock.patch("mozphab.mercurial.Mercurial.hg")
def test_commit(m_hg, hg):
    hg.commit("some body")
    m_hg.called_once()


@mock.patch("mozphab.mercurial.Mercurial.checkout")
@mock.patch("mozphab.mercurial.Mercurial.hg_out")
@mock.patch("mozphab.mercurial.Mercurial.hg")
@mock.patch("mozphab.mercurial.config")
def test_before_patch(m_config, m_hg, m_hg_out, m_checkout, hg):
    class Args:
        def __init__(
            self,
            rev_id="D123",
            nocommit=False,
            raw=False,
            applyto="base",
            no_bookmark=False,
        ):
            self.rev_id = rev_id
            self.nocommit = nocommit
            self.raw = raw
            self.applyto = applyto
            self.no_bookmark = no_bookmark

    m_config.create_bookmark = True
    m_hg_out.side_effect = ["bookmark"]
    hg.args = Args()
    hg.before_patch("sha1", "bookmark")
    m_checkout.assert_called_with("sha1")
    m_hg_out.assert_called()
    m_hg.assert_called_with(["bookmark", "bookmark_1"])
    m_checkout.assert_called_once_with("sha1")

    m_checkout.reset_mock()
    hg.args = Args(nocommit=True)
    m_hg.reset_mock()
    hg.before_patch("sha1", None)
    m_hg.assert_not_called()
    m_checkout.assert_called_once_with("sha1")

    hg.args = Args(applyto="here")
    m_checkout.reset_mock()
    m_hg_out.reset_mock()
    m_hg_out.side_effect = None
    m_hg_out.return_value = "some book_marks"
    hg.before_patch(None, "bookmark")
    m_hg_out.assert_called_once()
    m_checkout.assert_not_called()

    hg.args = Args(applyto="here")
    m_checkout.reset_mock()
    m_hg_out.reset_mock()
    m_hg_out.side_effect = None
    m_hg_out.return_value = "some book_marks"
    hg.before_patch(None, "bookmark")
    m_hg_out.assert_called_once()
    m_checkout.assert_not_called()

    m_hg_out.reset_mock()
    hg.args = Args(no_bookmark=True)
    hg.before_patch(None, "bookmark")
    m_hg_out.assert_not_called()

    m_config.create_bookmark = False
    m_hg_out.reset_mock()
    hg.args = Args()
    hg.before_patch(None, "bookmark")
    m_hg_out.assert_not_called()


@mock.patch("mozphab.mercurial.temporary_binary_file")
@mock.patch("mozphab.mercurial.Mercurial.hg")
def test_apply_patch(m_hg, m_temp_bin_fn, hg):
    m_temp_bin_fn.return_value = create_temp_fn("diff_fn")
    hg.apply_patch("diff", "body", "user", 1)
    m_hg.assert_called_once_with(["import", "diff_fn", "--quiet"])
    m_temp_bin_fn.assert_called_once_with(
        b"# HG changeset patch\n# User user\n# Date 1 0\nbody\ndiff"
    )


@mock.patch("mozphab.mercurial.Mercurial.hg_out")
def test_is_node(m_hg_out, hg):
    assert hg.is_node("aabbcc")
    m_hg_out.assert_called_once_with(["identify", "-q", "-r", "aabbcc"])

    m_hg_out.side_effect = mock.Mock(side_effect=exceptions.CommandError)
    assert not hg.is_node("aaa")


@mock.patch("mozphab.mercurial.Mercurial.is_node")
def test_check_node(m_is_node, hg):
    node = "aabbcc"
    m_is_node.return_value = True
    assert node == hg.check_node(node)

    m_is_node.return_value = False
    with pytest.raises(exceptions.NotFoundError) as e:
        hg.check_node(node)

    assert "" == str(e.value)


@mock.patch("mozphab.mercurial.Mercurial.hg_out")
def test_hg_cat(m_hg, hg):
    cat = m_hg.return_value = b"some text"
    hg.hg_cat("fn", "node")
    m_hg.assert_called_once_with(
        ["cat", "-r", "node", "fn"], expect_binary=True, split=False
    )
    assert cat == b"some text"


@mock.patch("mozphab.mercurial.Mercurial.hg_out")
def test_file_size(m_hg, hg):
    m_hg.return_value = "123\n"
    res = hg._file_size("fn", "rev")
    m_hg.assert_called_once_with(
        ["files", "-v", "-r", "rev", mock.ANY, "-T", "{size}"], split=False
    )
    assert res == 123


@mock.patch("mozphab.mercurial.Mercurial._file_size")
@mock.patch("mozphab.mercurial.Mercurial.hg_cat")
def test_file_meta(m_cat, m_file_size, hg):
    size = environment.MAX_TEXT_SIZE - 1
    m_file_size.return_value = size
    m_cat.return_value = b"spam\nham"
    meta = hg._get_file_meta("fn", "rev")
    assert meta == dict(
        binary=False,
        mime="TEXT",
        bin_body=b"spam\nham",
        body="spam\nham",
        file_size=size,
    )


@mock.patch("mozphab.mercurial.mimetypes")
@mock.patch("mozphab.mercurial.Mercurial._file_size")
@mock.patch("mozphab.mercurial.Mercurial.hg_cat")
def test_file_meta_binary(m_cat, m_file_size, m_mime, hg):
    m_mime.guess_type.return_value = ["MIMETYPE"]
    m_cat.return_value = b"spam\nham"
    size = environment.MAX_TEXT_SIZE + 1
    m_file_size.return_value = size
    meta = hg._get_file_meta("fn", "rev")
    assert meta == dict(
        binary=True,
        mime="MIMETYPE",
        bin_body=b"spam\nham",
        body=b"spam\nham",
        file_size=size,
    )

    hg._get_file_meta.cache_clear()
    hg.hg_cat.cache_clear()
    hg._file_size.cache_clear()

    size = environment.MAX_TEXT_SIZE - 1
    m_file_size.return_value = size
    m_cat.return_value = b"\0spam\nham"
    meta = hg._get_file_meta("fn", "rev")
    assert meta == dict(
        binary=True,
        mime="MIMETYPE",
        bin_body=b"\0spam\nham",
        body=b"\0spam\nham",
        file_size=size,
    )


@mock.patch("mozphab.mercurial.Mercurial._get_file_meta")
@mock.patch("mozphab.mercurial.Mercurial._change_set_binary")
@mock.patch("mozphab.mercurial.Mercurial._change_create_hunk")
def test_change_add(m_hunk, m_binary, m_meta, hg):
    change = None
    m_meta.return_value = dict(binary=False, body="abc\n", file_size=123)
    hg._change_add(change, "fn", None, "parent", "node")
    m_hunk.assert_called_once_with(
        change, "fn", ["+abc\n"], 123, "parent", "node", 0, 1, 0, 1
    )
    m_binary.assert_not_called()

    m_hunk.reset_mock()
    m_meta.return_value = dict(
        binary=True, bin_body=b"abc\n", body=b"abc\n", file_size=123, mime="MIME"
    )
    hg._change_add(change, "fn", None, "parent", "node")
    m_hunk.assert_not_called()
    m_binary.assert_called_once_with(change, "", b"abc\n", "", "MIME")


@mock.patch("mozphab.mercurial.Mercurial._get_file_meta")
@mock.patch("mozphab.mercurial.Mercurial._change_set_binary")
@mock.patch("mozphab.mercurial.Mercurial._change_create_hunk")
def test_change_del(m_hunk, m_binary, m_meta, hg):
    change = None
    m_meta.return_value = dict(
        binary=False, bin_body=b"abc\n", body="abc\n", file_size=123
    )
    hg._change_del(change, "fn", None, "parent", "node")
    m_hunk.assert_called_once_with(
        change, "fn", ["-abc\n"], 123, "parent", "node", 1, 0, 1, 0
    )
    m_binary.assert_not_called()

    m_hunk.reset_mock()
    m_meta.return_value = dict(
        binary=True, bin_body=b"abc\n", body=b"abc\n", file_size=123, mime="MIME"
    )
    hg._change_del(change, "fn", None, "parent", "node")
    m_hunk.assert_not_called()
    m_binary.assert_called_once_with(change, b"abc\n", "", "MIME", "")


@mock.patch("mozphab.mercurial.Mercurial._get_file_meta")
@mock.patch("mozphab.mercurial.Mercurial._change_set_binary")
@mock.patch("mozphab.mercurial.Mercurial._change_create_hunk")
@mock.patch("mozphab.mercurial.Mercurial.hg_out")
def test_change_mod(m_hg, m_hunk, m_binary, m_meta, hg):
    class Args:
        def __init__(self, lesscontext=False):
            self.lesscontext = lesscontext

    change = None
    text_side_effect = (
        dict(binary=False, bin_body=b"abc\n", body="abc\n", file_size=4),
        dict(binary=False, bin_body=b"def\n", body="def\n", file_size=4),
    )
    m_meta.side_effect = text_side_effect
    m_hg.return_value = b"""\
diff --git a/fn b/fn
--- a/B
+++ b/B
@@ -1,1 +1,1 @@
-abc
+def
"""
    hg.args = Args()
    hg._change_mod(change, "fn", "old_fn", "parent", "node")
    m_hg.assert_called_once_with(
        ["diff", "--git", "-U%s" % environment.MAX_CONTEXT_SIZE, "-r", "parent", "fn"],
        expect_binary=True,
    )
    m_hunk.assert_called_once_with(
        change, "fn", ["-abc\n", "+def\n"], 4, "parent", "node", 1, 1, 1, 1
    )

    m_hg.reset_mock()
    m_meta.side_effect = text_side_effect
    hg.args = Args(lesscontext=True)
    hg._change_mod(change, "fn", "old_fn", "parent", "node")
    m_hg.assert_called_once_with(
        ["diff", "--git", "-U100", "-r", "parent", "fn"], expect_binary=True
    )

    m_hunk.reset_mock()
    m_meta.side_effect = (
        dict(binary=True, bin_body=b"abc\n", body=b"abc\n", file_size=4, mime="MIME"),
        dict(binary=False, bin_body=b"def\n", body="def\n", file_size=4, mime="TEXT"),
    )
    hg._change_mod(change, "fn", "old_fn", "parent", "node")
    m_binary.assert_called_once_with(change, b"abc\n", b"def\n", "MIME", "TEXT")


def test_set_binary(hg):
    change = mock.Mock()
    hg._change_set_binary(change, b"a", b"b", "pdf/", "pdf/")
    assert change.binary
    assert change.uploads == [
        {"type": "old", "value": b"a", "mime": "pdf/", "phid": None},
        {"type": "new", "value": b"b", "mime": "pdf/", "phid": None},
    ]
    assert change.file_type.name == "BINARY"

    hg._change_set_binary(change, b"a", b"b", "image/jpeg", "pdf/")
    assert change.file_type.name == "IMAGE"

    hg._change_set_binary(change, b"a", b"b", "image/jpeg", "pdf/")
    assert change.file_type.name == "IMAGE"


@mock.patch("mozphab.mercurial.Mercurial.hg_out")
def test_get_file_modes(m_hg, hg):
    m_hg.side_effect = (
        ["  file name"],  # status
        [" :file name"],  # files - parent
        [" :file name"],  # files - node
    )
    actual = hg._get_file_modes(dict(node="aaa", parent="bbb"))
    expected = {"file name": dict(old_mode="100644", new_mode="100644")}
    assert actual == expected

    m_hg.side_effect = (
        ["M file name"],  # status
        [" :file name"],  # files - parent
        ["x:file name"],  # files - node
    )
    actual = hg._get_file_modes(dict(node="aaa", parent="bbb"))
    expected = {"file name": dict(old_mode="100644", new_mode="100755")}
    assert actual == expected

    m_hg.side_effect = (
        ["A file name"],  # status
        [],  # files - parent
        ["x:file name"],  # files - node
    )
    actual = hg._get_file_modes(dict(node="aaa", parent="bbb"))
    expected = {"file name": dict(new_mode="100755")}
    assert actual == expected

    m_hg.side_effect = (
        ["R file name"],  # status
        [" :file name"],  # files - parent
        [],  # files - node
    )
    actual = hg._get_file_modes(dict(node="aaa", parent="bbb"))
    expected = {"file name": dict(old_mode="100644")}
    assert actual == expected


def test_check_vcs(hg):
    class Args:
        def __init__(self, force_vcs=False):
            self.force_vcs = force_vcs

    hg.args = Args()
    assert hg.check_vcs()

    hg._phab_vcs = "git"
    with pytest.raises(exceptions.Error):
        hg.check_vcs()

    hg.args = Args(force_vcs=True)
    assert hg.check_vcs()


@mock.patch("mozphab.mercurial.hglib.open")
@mock.patch("mozphab.repository.Repository._phab_url")
@mock.patch("os.path.isdir")
@mock.patch("mozphab.helpers.which")
def test_repository_cached(m_which, m_is_dir, m_phab_url, m_open, *patched):
    class MyRepo:
        version = 4, 7, 3

        def rawcommand(self, *args, **kw):
            return b"ui.username=xxx"

    m_is_dir.return_value = True
    m_phab_url.return_value = ""
    m_which.return_value = True
    m_open.return_value = MyRepo()

    hg = Mercurial("x")

    class Args:
        def __init__(self):
            self.start_rev = "(auto)"
            self.end_rev = "."
            self.safe_mode = True
            self.single = False

    hg._repo = None
    hg.set_args(Args())
    current_repo = hg.repository
    assert current_repo is not None

    # makes sure we cache the `hgclient` instance when using the
    # same args
    hg.set_args(Args())
    assert hg.repository is current_repo
