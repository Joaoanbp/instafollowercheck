"""Parsers e paginação do coletor, sem abrir navegador ou acessar a rede."""

from __future__ import annotations

import unittest

from instafollowcheck.collector import (
    ActionRequired,
    Cancelled,
    IncompleteData,
    LoginRequired,
    RateLimited,
    api_error,
    collect_pages,
    find_profile_info,
    parse_friendship_payload,
    parse_profile_payload,
    validate_counts,
)


class CollectorParserTests(unittest.TestCase):
    def test_profile_payload(self):
        payload = {
            "data": {
                "user": {
                    "id": "100",
                    "username": "MinhaConta",
                    "edge_followed_by": {"count": 2},
                    "edge_follow": {"count": 3},
                }
            }
        }
        info = parse_profile_payload(payload)
        self.assertEqual(info.owner_id, "100")
        self.assertEqual(info.username, "minhaconta")
        self.assertEqual(info.followers, 2)
        self.assertEqual(info.following, 3)

    def test_profile_payload_missing_data(self):
        with self.assertRaises(IncompleteData):
            parse_profile_payload({"data": {}})

    def test_embedded_profile_json_finds_nested_user(self):
        payload = {
            "require": [
                [
                    None,
                    {
                        "__bbox": {
                            "result": {
                                "data": {
                                    "user": {
                                        "id": "1682931520",
                                        "username": "Juxxny",
                                        "follower_count": 280,
                                        "following_count": 521,
                                    }
                                }
                            }
                        }
                    },
                ]
            ],
            "suggestions": [
                {
                    "id": "999",
                    "username": "outra",
                    "follower_count": 1,
                    "following_count": 1,
                }
            ],
        }
        info = find_profile_info([payload], "juxxny")
        self.assertIsNotNone(info)
        self.assertEqual(info.owner_id, "1682931520")
        self.assertEqual(info.username, "juxxny")
        self.assertEqual(info.followers, 280)
        self.assertEqual(info.following, 521)

    def test_embedded_profile_json_accepts_legacy_edges(self):
        payload = {
            "deep": {
                "user": {
                    "id": 100,
                    "username": "ana",
                    "edge_followed_by": {"count": 12},
                    "edge_follow": {"count": 34},
                }
            }
        }
        info = find_profile_info([payload], "ana")
        self.assertEqual(info.owner_id, "100")
        self.assertEqual(info.followers, 12)
        self.assertEqual(info.following, 34)

    def test_embedded_profile_json_requires_matching_username_and_counts(self):
        payloads = [
            {"user": {"id": "1", "username": "outra", "follower_count": 1, "following_count": 1}},
            {"user": {"id": "2", "username": "ana", "follower_count": 3}},
            {"user": {"id": "3", "username": "ana", "following_count": 4}},
        ]
        self.assertIsNone(find_profile_info(payloads, "ana"))
        self.assertIsNone(find_profile_info([{"user": None}, [None], "texto"], "ana"))

    def test_embedded_profile_json_reads_the_requested_account(self):
        payload = {
            "users": [
                {
                    "id": "10",
                    "username": "ana",
                    "follower_count": 5,
                    "following_count": 6,
                },
                {
                    "id": "20",
                    "username": "bia",
                    "follower_count": 7,
                    "following_count": 8,
                },
            ]
        }
        info = find_profile_info([payload], "bia")
        self.assertEqual(info.owner_id, "20")
        self.assertEqual(info.followers, 7)
        self.assertEqual(info.following, 8)

    def test_rest_friendship_payload(self):
        payload = {
            "users": [
                {"pk": 1, "username": "ana"},
                {"pk": 2, "username": "bia"},
            ],
            "next_max_id": "cursor-1",
        }
        users, cursor = parse_friendship_payload(payload)
        self.assertEqual(users, {"id:1": "ana", "id:2": "bia"})
        self.assertEqual(cursor, "cursor-1")

    def test_graphql_friendship_payload(self):
        payload = {
            "data": {
                "user": {
                    "edge_followed_by": {
                        "edges": [{"node": {"id": "9", "username": "carla"}}],
                        "page_info": {"has_next_page": True, "end_cursor": "next"},
                    }
                }
            }
        }
        users, cursor = parse_friendship_payload(payload)
        self.assertEqual(users, {"id:9": "carla"})
        self.assertEqual(cursor, "next")

    def test_invalid_entries_are_ignored(self):
        users, cursor = parse_friendship_payload(
            {"users": [None, {"username": "http://invalido"}, {"pk": 1}]}
        )
        self.assertEqual(users, {})
        self.assertIsNone(cursor)

    def test_validate_counts(self):
        note = validate_counts(100, 99, "seguidores")
        self.assertIn("100", note)
        self.assertIn("99", note)
        self.assertEqual(validate_counts(100, 100, "seguidores"), "")
        self.assertEqual(validate_counts(None, 100, "seguidores"), "")
        with self.assertRaises(IncompleteData):
            validate_counts(100, 80, "seguidores")

    def test_api_errors_are_actionable(self):
        with self.assertRaises(LoginRequired):
            api_error({"message": "login_required"}, 401)
        with self.assertRaises(RateLimited):
            api_error({"message": "please wait"}, 429)
        with self.assertRaises(ActionRequired):
            api_error({"message": "feedback_required"}, 400)
        with self.assertRaises(LoginRequired):
            api_error({"message": "checkpoint_required"}, 400)


class PaginationTests(unittest.TestCase):
    def test_collects_all_pages_and_deduplicates(self):
        pages = {
            None: {"users": [{"pk": 1, "username": "ana"}], "next_max_id": "c1"},
            "c1": {
                "users": [{"pk": 1, "username": "ana"}, {"pk": 2, "username": "bia"}],
                "next_max_id": "c2",
            },
            "c2": {"users": [{"pk": 3, "username": "caio"}]},
        }
        cursors = []

        def fetch(cursor):
            cursors.append(cursor)
            return pages[cursor]

        users = collect_pages(fetch, 3, "followers", pacer=lambda: None)
        self.assertEqual(cursors, [None, "c1", "c2"])
        self.assertEqual(set(users.values()), {"ana", "bia", "caio"})
        self.assertEqual(len(users), 3)

    def test_repeated_cursor_stops(self):
        def fetch(cursor):
            return {"users": [{"pk": 1, "username": "ana"}], "next_max_id": "same"}

        users = collect_pages(fetch, 2, "followers", pacer=lambda: None)
        self.assertEqual(users, {"id:1": "ana"})

    def test_duplicate_page_with_new_cursor_continues(self):
        pages = {
            None: {"users": [{"pk": 1, "username": "ana"}], "next_max_id": "c1"},
            "c1": {"users": [{"pk": 1, "username": "ana"}], "next_max_id": "c2"},
            "c2": {"users": [{"pk": 1, "username": "ana"}, {"pk": 2, "username": "bia"}]},
        }

        users = collect_pages(lambda cursor: pages[cursor], 2, "followers", pacer=lambda: None)
        self.assertEqual(set(users.values()), {"ana", "bia"})

    def test_three_empty_pages_stop_pagination(self):
        counter = {"page": 0, "fetches": 0}

        def fetch(cursor):
            counter["fetches"] += 1
            counter["page"] += 1
            return {
                "users": [{"pk": 1, "username": "ana"}],
                "next_max_id": f"empty-{counter['page']}",
            }

        users = collect_pages(fetch, 1, "followers", pacer=lambda: None)
        self.assertEqual(users, {"id:1": "ana"})
        self.assertEqual(counter["fetches"], 4)

    def test_cancel_before_first_page(self):
        with self.assertRaises(Cancelled):
            collect_pages(lambda cursor: {}, 1, "followers", cancel=lambda: True)

    def test_page_limit_is_protected(self):
        counter = {"page": 0}

        def fetch(cursor):
            counter["page"] += 1
            number = counter["page"]
            return {
                "users": [{"pk": number, "username": f"user{number}"}],
                "next_max_id": f"cursor-{number}",
            }

        with self.assertRaises(IncompleteData):
            collect_pages(
                fetch,
                10,
                "following",
                pacer=lambda: None,
                max_pages=2,
            )

    def test_empty_first_page_with_expected_count_fails(self):
        with self.assertRaises(IncompleteData):
            collect_pages(lambda cursor: {"users": []}, 5, "followers")


if __name__ == "__main__":
    unittest.main()
