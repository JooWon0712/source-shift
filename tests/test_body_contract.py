import unittest

from source_shift.body_contract import validate_body


class BodyContractTests(unittest.TestCase):
    def assert_allowed(self, route, bodies):
        for body in bodies:
            with self.subTest(route=route, body=body):
                original = body
                self.assertIsNone(validate_body(route, body))
                self.assertEqual(body, original)

    def assert_rejected(self, route, bodies):
        for body in bodies:
            with self.subTest(route=route, body=body):
                with self.assertRaises(ValueError):
                    validate_body(route, body)

    def test_c_nested_blocks_and_digraph_braces(self):
        self.assert_allowed("c", (
            "", "return a + b + c;", "if (a) { return b; } else { return c; }",
            "<% int x = a; { x += b; } return x; %>",
            "if (a) <% return b; %> return c;",
        ))

    def test_c_comments_and_quoted_delimiters(self):
        self.assert_allowed("c", (
            "/* } # %> <% */ return a;", "// } # %> <%\nreturn a;",
            "// comment at end",
            'const char *s = "} # %> /* //"; return a;',
            'const char *s = "\\\" }"; return a;',
            "char x = '}'; char y = '\\''; return a;",
            'const char *s = "\\\\"; return a;',
        ))

    def test_c_splicing_happens_before_comments(self):
        self.assert_allowed("c", (
            "ret\\\nurn a;", "/\\\n* } # %> */ return a;",
            "// ignored \\\n} # %>\nreturn a;",
            "// ignored \\ \t\n} # %>\nreturn a;",
            "// ignored \\\r\n} # %>\r\nreturn a;",
            '// comment \\\n',
            'const char *s = "\\\n}"; return a;',
        ))

    def test_c_rejects_outer_close_even_if_later_reopened(self):
        self.assert_rejected("c", (
            "}", "} int other() { return 0; } int filler() {",
            "%>", "%> int other() <% return 0; %> int filler() <%",
            "%\\\n>", "{ } } {", "// comment\n}",
        ))

    def test_c_rejects_preprocessor_tokens_outside_literals(self):
        self.assert_rejected("c", (
            "#include <stdio.h>", "#define X 0", "%:define X 0",
            "%:%: foo", "%\\\n:define X 0", "\\\n#define X 0",
            "return a; #", "/* safe */ %:define X 0",
        ))

    def test_c_rejects_unfinished_lexical_tokens_and_blocks(self):
        self.assert_rejected("c", (
            "/*", "/* never closed", '"unterminated', "'unterminated",
            '"raw\nnewline"', '"escape\\', "'escape\\", "{", "<%",
            "// trailing escape\\", "// trailing escape\\ \t", "return a; \\",
        ))

    def test_c_rejects_trigraphs_before_lexing(self):
        self.assert_rejected("c", tuple("??" + ending for ending in "=/\'()!<>-"))
        self.assert_rejected("c", ('"??/"', "/* ??< */", "// ??= comment"))

    def test_wat_folded_and_flat_instructions(self):
        self.assert_allowed("wat", (
            "", "local.get $a\nlocal.get $b\ni32.add",
            "(i32.add (local.get $a) (local.get $b))",
            "(local $x i32) (block (loop $again (br $again)))",
        ))

    def test_wat_comments_are_nested_and_ignore_quotes(self):
        self.assert_allowed("wat", (
            ";; ) ( \" (;\nlocal.get $a",
            ";; comment at end )",
            "(; ) ( \" ;) local.get $a",
            "(; outer (; inner ) ( ;; \" ;) still outer ;) local.get $a",
            "(block (; a nested comment (; ;) ;) (i32.const 0) drop)",
        ))

    def test_wat_strings_do_not_affect_balance(self):
        self.assert_allowed("wat", (
            '"(; ;; ) ("', '"\\\" ) ("', '"\\22 ) ("',
            '"\\\\"', '(i32.const 0) "\\u{29}"',
        ))

    def test_wat_rejects_template_escape_and_unclosed_paren(self):
        self.assert_rejected("wat", (
            ")", ")(memory 1)(func", "(block) ) (func", "(block", "(",
            ";; ignored )\n)", "(; safe ;) )",
        ))

    def test_wat_rejects_comments_and_strings_that_swallow_template(self):
        self.assert_rejected("wat", (
            "(;", "(; unfinished", "(; outer (; inner ;) unfinished",
            '"unfinished', '"unfinished\\', '(i32.const 0) "(;',
        ))

    def test_unknown_route_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_body("raw-bytes", "")


if __name__ == "__main__":
    unittest.main()
