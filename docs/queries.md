# Writing queries

A query is a `.sql` file in the queries folder: the shared folder of a hybrid installation, or the queries folder of a server. It has three parts:

1. a header comment describing the query: its name, who can see it, on which servers it runs;
2. a `DECLARE` block listing its parameters, which are turned into input fields;
3. the SQL command itself.

Users never see the SQL: they pick a query, fill in its parameters and get the result.

## Example

```sql
/* Query infos
Code : SALES_BY_CUSTOMER
Description : Sales by customer over a period
Hide : 0
Grp_Authorized : sales, accounting
Servers : erp_prod
Version : 2026-11-08, site filter added
*/

DECLARE
@start AS date = '2026-01-01',        -- Start date|fiscal_year(12, -12, 1)
@end AS date = '2026-12-31',          -- End date|month_end(-1)
@site AS nvarchar(5) = '',            -- Site|optional|^[A-Z0-9]+$
@!table AS nvarchar(20) = 'SINVOICE'  -- Source|list(SINVOICE: Invoices, SORDER: Orders)
;

SELECT BPCINV_0 AS customer, SUM(AMTATI_0) AS amount
FROM @!table
WHERE ACCDAT_0 BETWEEN @start AND @end
  AND (@site = ' ' OR FCY_0 = @site)
GROUP BY BPCINV_0
```

The file is valid T-SQL: it can be run as is in SQL Server Management Studio while writing it, as the first `DECLARE` block gives the parameters their default values.

## Header

The header is the first comment block of the file. Its opening `/*` and closing `*/` must each start a line, the rest of the opening line being free (`/* Query infos` above). Each line inside holds one `Key : value` field. Keys are case insensitive, and a key Pytre doesn't know is ignored, so the header can also hold notes such as a version history.

| Key | Default | Meaning |
|---|---|---|
| `Code` | the file name | Name of the query in the list |
| `Description` | empty | Description shown next to the name |
| `Hide` | `0` | Who sees the query: `0` everyone allowed, `1` admins only, `2` nobody |
| `Grp_Authorized` | everyone | Comma-separated groups allowed to run the query |
| `Servers` | every server | Comma-separated identifiers of the SQL servers the query runs on |

- `Grp_Authorized` and `Servers` values are compared in lower case.
- A hidden query keeps a marker in front of its description: `(*)` for `Hide : 1`, `(**)` for `Hide : 2`.
- The file name, without `.sql`, identifies the query on a server: renaming a file makes clients see a new query, and they have to refresh their list.

### Who sees a query

A user sees a query in the list of a server when all of these are true:

- they are allowed on that server: the server has no groups, or they belong to one of them;
- the server is one of the query `Servers`, or the query has no `Servers`;
- they are allowed on the query: the query has no `Grp_Authorized`, or they belong to one of its groups;
- the query isn't hidden from them: `Hide : 0`, or `Hide : 1` and they are an admin.

Admins skip the group checks.  
Every user belongs to the `all` group, so a query without `Grp_Authorized` is open to everyone, except users whose *Restrict queries to the selected groups* option is set: they only see the queries shared with one of their own groups. To open a query to everyone, them included, list `all` with their group: `Grp_Authorized : all, sales`.  
Users and their groups are managed from the web administration of the server, or from the users window of a hybrid client.

## Parameters

Parameters, if any, are declared in the first `DECLARE` block, before the SQL command:

```sql
DECLARE
@name AS type = 'default value',	-- Label|options|pattern
;
```

- `DECLARE` must start a line, and the block ends at the first `;`: no `;` may appear inside it, comments included.
- Each parameter is on its own line, starting with `@` at the very beginning of the line.
- The name is `@` followed by letters, digits, `_`, `#`, `$` or `@`. A name starting with `@!` is an inlined parameter, see [Inlined parameters](#inlined-parameters).
- The default value is quoted, or unquoted up to the next comma or space. Dates are written in SQL format: `'2026-01-31'`.
- The comma between two declarations is only needed for the file to stay valid T-SQL.
- Everything Pytre needs about the parameter is in the `--` comment of its line: its label, then optional parts separated by `|`. The label and the options can't contain `|`.

In the SQL command, every declared parameter name is replaced by its value, wherever it appears as a whole name. Other variables, declared later in the command, are left as they are.

### Types

The type decides how the value typed by the user is checked and sent to the database.

| Type | Value |
|---|---|
| `int` | A whole number |
| `bit` | `0` or `1` |
| `date` | A date in the user's date format, or as `ddmmyy`, `ddmmyyyy`, `dd/mm/yy`, `dd/mm/yyyy`, sent as `YYYY-MM-DD` |
| `datetime` | The same followed by ` HH:MM:SS`, sent as `YYYY-MM-DD HH:MM:SS` |
| `char(n)`, `nchar(n)` | Exactly `n` characters |
| `varchar(n)`, `nvarchar(n)`, `text`, `ntext` | At most `n` characters, 255 without size or with `max` |
| any other type | Sent as typed, without check |

Use these type names in PostgreSQL queries too, for instance `int` rather than `integer`: only known types are checked.

An empty value is refused, unless the parameter is optional. An empty optional value is sent as `0` for an `int`, and as a single space otherwise: test it with `@site = ' '`, as in the example.

### Options

The second part of the comment holds comma-separated options. A comma inside parentheses belongs to the option.

| Option | Effect |
|---|---|
| `optional` | The parameter may be left empty |
| `hide` | The parameter isn't shown to users. Admins see it in red, marked `(*)`, and can change it |
| a computed value | Replaces the default value, see [Computed values](#computed-values) |
| `entry(…)`, `list(…)`, `check(…)` | The input field and its allowed values, see [Input fields](#input-fields) |
| `(…)` alone | Allowed values for a standard input field |

Through a server, the value of a hidden parameter is always computed by the server, whatever the client sends, unless the user is an admin.

### Pattern

The third part of the comment is an optional regular expression the value is expected to match, for instance `^[A-Z0-9]+$`. It may contain `|`. A value that doesn't match is only flagged: its field turns orange and a message shows the expected pattern, but the query can still be run. Use allowed values to enforce a value.

### Computed values

A computed value replaces the default value of the parameter each time the query is opened.

| Function | Value |
|---|---|
| `user_info(attribute)` | An attribute of the current user: `username`, `display_name`, `admin`, or a custom attribute defined in the users settings |
| `today(days)` | Today, moved by `days` |
| `month_end(months, days)` | The last day of the current month, moved by `months`, then by `days` |
| `fiscal_year(last_month, months, days, today_months)` | The last day of the current fiscal year, ending with month `last_month`, moved by `months`, then by `days`. `today_months` first moves today, to work out which fiscal year is the current one |

Every argument but `attribute` and `last_month` is optional and defaults to `0`. Examples, on November 8, 2026:

| Example | Value |
|---|---|
| `today()` | 2026-11-08 |
| `today(-7)` | 2026-11-01 |
| `month_end()` | 2026-11-30 |
| `month_end(-1)` | 2026-10-31, last day of the previous month |
| `month_end(-1, 1)` | 2026-11-01, first day of the current month |
| `fiscal_year(12)` | 2026-12-31 |
| `fiscal_year(12, -12, 1)` | 2026-01-01, first day of the fiscal year |
| `fiscal_year(6)` | 2027-06-30, for a fiscal year ending in June |
| `fiscal_year(6, -12, 1)` | 2026-07-01, first day of a fiscal year ending in June |
| `fiscal_year(12, 0, 0, -11)` | 2025-12-31, the fiscal year of 11 months ago |

### Input fields

An input field is `entry`, the default, `list` or `check`, followed by its allowed values in parentheses:

- `entry`: a standard input field. With allowed values, only those are accepted;
- `list`: a drop-down list of the allowed values;
- `check`: a check box. Its first value is sent when checked, its second when unchecked, as in `check(1, 0)`.

Each allowed value is written `sql_value: label`: `sql_value` is sent to the database, `label` is shown to the user, and is the same as `sql_value` when left out. Values are separated by commas, so neither values nor labels can contain `,`, `:` or parentheses. Labels are ignored by `check`.

The default value of the parameter is a `sql_value`, and must be one of the allowed values: otherwise the query can't be loaded.

| Example | Field |
|---|---|
| `entry(FR, EN)` | Input field accepting only `FR` or `EN` |
| `entry(FR: French, EN: English)` | The same, the user typing `French` or `English` |
| `list(1: French, 2: English)` | Drop-down list showing `French` and `English`, sending `1` or `2` |
| `check(1, 0)` | Check box sending `1` when checked, `0` otherwise |
| `check(2, 1)` | Check box sending `2` when checked, `1` otherwise |

For an optional parameter with allowed values, add an empty value so that it can be chosen, and use it as the default value if the parameter starts empty: `@type AS nvarchar(1) = ''	-- Type|optional, list(: All, 1: French, 2: English)`.

### Inlined parameters

A parameter whose name starts with `@!` is written as is into the SQL text, instead of being passed to the database as a query parameter. It lets a query choose, for instance, a schema or a table: `FROM @!table`.

As its value becomes part of the SQL, an inlined parameter can only use `list` or `check`, so that its value is always one of the allowed values: a free input field would allow SQL injection, a query using one is rejected when loading and doesn't appear in the list.

## How a query is run

- The `DECLARE` block and everything before it are left out: only the command after the block is sent.
- Parameters, except inlined ones, are sent to the database as query parameters, never written into the SQL text. This works the same way for SQL Server and PostgreSQL.
- In a PostgreSQL query, write `%%` for a `%` character, as in `LIKE 'abc%%'`: the PostgreSQL drivers would read a single `%` as a parameter marker. In a SQL Server query, write a single `%`.
- Through a server, the client only sends the query identifier and the parameter values: the server reads the query from its own folder and checks the values again. See [Server security](../README.md#server-security).

Query files are read as UTF-8, or as Windows-1252 when they aren't valid UTF-8.
