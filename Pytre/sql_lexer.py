import re
from enum import Enum
from pathlib import Path

from sql_keywords import sql_keywords


class TokenType(Enum):
    COMMENT = "comment"
    TEXT = "text"
    KEYWORD = "keyword"
    IDENTIFIER = "identifier"
    PARAMETER = "parameter"
    NUMBER = "number"
    OPERATOR = "operator"
    DELIMITER = "delimiter"
    WHITESPACE = "whitespace"
    UNKNOWN = "unknown"


class Token:
    def __init__(self, start: int, length: int, type: TokenType, value: str = ""):
        self.pos: int = start
        self.length: int = length
        self.type: TokenType = type
        self.value: str = value

    def __repr__(self):
        return str({"start": self.pos, "length": self.length, "type": self.type, "value": self.value})

    def __str__(self):
        return str({"start": self.pos, "length": self.length, "type": self.type.name, "value": self.value})


class SqlLexer:
    keywords = [keyword for keyword in map(str.casefold, sql_keywords)]

    def __init__(self, sql_text: str = ""):
        self.sql_text: str = sql_text
        self.sql_length: int = 0
        self.tokens: dict[Token] = {}

        self.init_tests_order()
        self.init_tokens_to_throw()
        self.tokenize()

    def tokens_get(self, type: TokenType = None):
        if type is None:
            return self.tokens.items()
        else:
            return [token for _, token in self.tokens.items() if token.type is type]

    def tokens_print(self, type: TokenType = None):
        for key, token in self.tokens.items():
            if type is None or token.type is type:
                print(f"{key}: {token}")

    def init_tests_order(self):
        self.tests_order = [
            self.t_NESTED_COMMENT,
            self.t_COMMENT,
            self.t_TEXT,
            self.t_KEYWORD,
            self.t_IDENTIFIER,
            self.t_PARAMETER,
            self.t_NUMBER,
            self.t_OPERATOR,
            self.t_DELIMITER,
            self.t_WHITESPACE,
        ]

    def init_tokens_to_throw(self):
        self.tokens_to_throw = [
            TokenType.WHITESPACE,
            TokenType.UNKNOWN,
        ]

    def tokenize(self):
        self.sql_length = len(self.sql_text)
        i, key = 0, 0
        while i < self.sql_length:
            for test in self.tests_order:
                token = test(i)
                if token:
                    if token.length == 0:
                        raise Exception(f"Match length is 0, modify regex for {test.__name__}")

                    if token.type not in self.tokens_to_throw:
                        self.tokens[key] = token
                        key += 1

                    i += token.length
                    break
            else:
                if TokenType.UNKNOWN not in self.tokens_to_throw:
                    self.tokens[key] = Token(i, 1, TokenType.COMMENT, self.sql_text[i])
                    key += 1

                i += 1

    def basic_test(self, regex: str, text: str, pos: int, flags: re.RegexFlag, type: TokenType) -> Token | None:
        pattern: re.Pattern = re.compile(regex, flags)
        match: re.Match = pattern.match(text, pos)
        if match:
            length = match.end() - match.start()
            return Token(match.pos, length, type, match.group())

    def nested_test(self, regex_start: str, regex_end: str, text: str, pos: int, type: TokenType) -> Token | None:
        text_length = len(text)
        regex: str = rf"({regex_start})|({regex_end})"  # to match either start or end of comment

        pattern: re.Pattern = re.compile(regex, re.NOFLAG)
        pat_start: re.Pattern = re.compile(regex_start, re.NOFLAG)

        i, level = pos, 0
        while i < text_length:
            match: re.Match = pat_start.match(text, i) if level == 0 else pattern.search(text, i)
            if not match:
                return
            value = match.group()
            level += 1 if pat_start.match(value) else -1

            if level == 0:
                pos_end = match.end()
                length = pos_end - pos
                value = text[pos:pos_end]
                return Token(pos, length, type, value)
            else:
                i = match.end() + 1

    def t_NESTED_COMMENT(self, pos: int, text: str = "") -> Token | None:
        text = self.sql_text if text == "" else text
        regex_start: str = r"/\*"  # to match start of comment
        regex_end: str = r"\*/"  # to match end of comment
        token: Token = self.nested_test(regex_start, regex_end, text, pos, TokenType.COMMENT)
        return token

    def t_COMMENT(self, pos: int, text: str = "") -> Token | None:
        text = self.sql_text if text == "" else text
        regex: str = r"--.*?$"
        flags = re.MULTILINE
        token: Token = self.basic_test(regex, text, pos, flags, TokenType.COMMENT)
        return token

    def t_TEXT(self, pos: int, text: str = "") -> Token | None:
        text = self.sql_text if text == "" else text
        regex: str = r"\'.*?\'"
        flags = re.DOTALL
        token: Token = self.basic_test(regex, text, pos, flags, TokenType.TEXT)
        return token

    def t_KEYWORD(self, pos: int, text: str = "") -> Token | None:
        text = self.sql_text if text == "" else text
        regex: str = r"_?[_a-zA-Z0-9]+"
        flags = re.NOFLAG
        token: Token = self.basic_test(regex, text, pos, flags, TokenType.KEYWORD)
        if token and token.value.casefold() in SqlLexer.keywords:
            return token

    def t_IDENTIFIER(self, pos: int, text: str = "") -> Token | None:
        text = self.sql_text if text == "" else text
        # fmt: off
        regex:str = "(" +  r"[a-zA-Z_][a-zA-Z0-9_@$#.]*"  + ")|" \
                    "(" +  r"\[.*?\]"                     + ")"
        # fmt: on
        flags = re.DOTALL
        token: Token = self.basic_test(regex, text, pos, flags, TokenType.IDENTIFIER)
        return token

    def t_PARAMETER(self, pos: int, text: str = "") -> Token | None:
        text = self.sql_text if text == "" else text
        # fmt: off
        regex:str = "(" +  r"@!?[\d\w#\$@]+"        + ")|" \
                    "(" +  r"%\(@[\d\w#\$@]+\)s"  + ")"
        # fmt: on
        flags = re.NOFLAG
        token: Token = self.basic_test(regex, text, pos, flags, TokenType.PARAMETER)
        return token

    def t_NUMBER(self, pos: int, text: str = "") -> Token | None:
        text = self.sql_text if text == "" else text
        # fmt: off
        regex:str = "(" +  r"-?\d+(e[+-]?\d+)?"       + ")|" \
                    "(" +  r"-?\d+\.\d*(e[+-]?\d+)?"  + ")|" \
                    "(" +  r"-?\d*\.\d+(e[+-]?\d+)?"  + ")|" \
                    "(" +  r"0x[0-9A-F]+"             + ")|" \
                    "(" +  r"0o[0-7]+"                + ")|" \
                    "(" +  r"0b[0-1]+"                + ")"
        # fmt: on
        flags = re.NOFLAG
        token: Token = self.basic_test(regex, text, pos, flags, TokenType.NUMBER)
        return token

    def t_OPERATOR(self, pos: int, text: str = "") -> Token | None:
        text = self.sql_text if text == "" else text
        # fmt: off
        regex:str = "(" +  r"\|\*="           + ")|" \
                    "(" +  r"[><+\-*/%&]?="   + ")|" \
                    "(" +  r"<>"              + ")|" \
                    "(" +  r"[<>]"            + ")|" \
                    "(" +  r"[+\-*/%^]"       + ")|" \
                    "(" +  r"[&]"             + ")"
        # fmt: on
        flags = re.NOFLAG
        token: Token = self.basic_test(regex, text, pos, flags, TokenType.OPERATOR)
        return token

    def t_DELIMITER(self, pos: int, text: str = "") -> Token | None:
        text = self.sql_text if text == "" else text
        regex: str = r";"
        flags = re.NOFLAG
        token: Token = self.basic_test(regex, text, pos, flags, TokenType.DELIMITER)
        return token

    def t_WHITESPACE(self, pos: int, text: str = "") -> Token | None:
        text = self.sql_text if text == "" else text
        regex: str = r"[\s]"
        flags = re.NOFLAG
        token: Token = self.basic_test(regex, text, pos, flags, TokenType.WHITESPACE)
        return token


if __name__ == "__main__":
    filenames = (Path.cwd() / "Requetes SQL").glob("*.sql")
    filename = ""
    for i, filename in enumerate(filenames):
        if i == 3:
            break

    if filename:
        with open(filename, "r", encoding="utf-8") as file:
            sql_text = "\n".join(file.readlines())

        my_lexer = SqlLexer(sql_text)
        my_lexer.tokens_print()
