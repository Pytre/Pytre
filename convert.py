import re
from datetime import datetime


class Convert:
    def __init__(self, date_txt_format="%d/%m/%Y", field_separator=";", decimal_separator=","):
        self.date_txt_format = date_txt_format  # pour affichage utilisateurs ou extraction
        self.date_val_format = "%Y-%m-%d"  # pour commande SQL

        self.datetime_txt_format = "%d/%m/%Y %H:%M:%S"  # pour affichage utilisateurs ou extraction
        self.datetime_val_format = "%Y-%m-%d %H:%M:%S"  # pour commande SQL

        self.field_separator = field_separator
        self.decimal_separator = decimal_separator

        self.cls_to_cmd = _ToCmd(self)
        self.cls_to_display = _ToDisplay(self)
        self.cls_from_result = _FromResult(self)

    def to_cmd(self, type_name: str, string_to_convert: str, type_args=[]) -> str:
        return self.cls_to_cmd.transform(type_name, string_to_convert, type_args)

    def to_display(self, type_name: str, value_to_convert: str) -> str:
        return self.cls_to_display.transform(type_name, value_to_convert)

    def from_result(self, value) -> str:
        return self.cls_from_result.transform(value)


class _ToCmd:
    def __init__(self, parent):
        self.parent: Convert = parent

    def transform(self, type_name: str, string_to_convert: str, type_args=[]) -> str:
        if string_to_convert == "":
            return self._convert_to_null_value(type_name)

        if self.func_dict().get(type_name):
            if type_args == []:
                return self.func_dict()[type_name](string_to_convert)
            else:
                return self.func_dict()[type_name](string_to_convert, type_args)
        else:
            return string_to_convert

    def func_dict(self) -> dict:
        my_dict = {
            "bit": self._str_to_bit,
            "int": self._str_to_int,
            "date": self._str_to_date,
            "datetime": self._str_to_datetime,
            "nchar": self._str_tochar,
            "char": self._str_tochar,
            "varchar": self._str_to_nvarchar,
            "nvarchar": self._str_to_nvarchar,
            "text": self._str_to_nvarchar,
            "ntext": self._str_to_nvarchar,
        }

        return my_dict

    def _str_to_bit(self, string_to_convert: str) -> int:
        try:
            value = int(string_to_convert)
            if value != 0 and value != 1:
                raise ValueError(f"Erreur valeur, {string_to_convert} ne peut être que 0 ou 1")
        except ValueError:
            if value != 0 and value != 1:
                raise ValueError(f"{string_to_convert} ne peut être que 0 ou 1")
            else:
                raise ValueError(f"{string_to_convert} n'est pas un nombre entier valide")

        return value

    def _str_to_int(self, string_to_convert: str) -> int:
        try:
            value = int(string_to_convert)
        except ValueError:
            raise ValueError(f"{string_to_convert} n'est pas un nombre entier valide")

        return value

    def _str_to_date(self, string_to_convert: str) -> str:
        valid_format = ["%d%m%y", "%d%m%Y", "%d/%m/%y", "%d/%m/%Y"]

        # mettre en premier format spécifié par les settings
        if self.parent.date_txt_format in valid_format:
            valid_format.remove(self.parent.date_txt_format)
        valid_format.insert(0, self.parent.date_txt_format)

        for i, my_format in enumerate(valid_format):
            try:
                my_date = datetime.strptime(string_to_convert, my_format)
                value = my_date.strftime(self.parent.date_val_format)
                break
            except ValueError:
                if i + 1 == len(valid_format):
                    raise ValueError(f"{string_to_convert} n'est pas une date valide (jj/mm/aaaa)")

        return value

    def _str_to_datetime(self, string_to_convert: str) -> str:
        valid_format = ["%d%m%y %H:%M:%S", "%d%m%Y %H:%M:%S", "%d/%m/%y %H:%M:%S", "%d/%m/%Y %H:%M:%S"]

        # mettre en premier format spécifié par les settings
        if self.parent.date_txt_format in valid_format:
            valid_format.remove(self.parent.datetime_txt_format)
        valid_format.insert(0, self.parent.datetime_txt_format)

        for i, my_format in enumerate(valid_format):
            try:
                my_date = datetime.strptime(string_to_convert, my_format)
                value = my_date.strftime(self.parent.date_val_format)
                break
            except ValueError:
                if i + 1 == len(valid_format):
                    raise ValueError(f"{string_to_convert} n'est pas une date valide (jj/mm/aaaa hh:mm:ss)")

        return value

    def _str_to_nvarchar(self, string_to_convert: str, type_args: list[str] = []) -> str:
        max_size = 255 if type_args[0] == "max" or type_args == [] else int(type_args[0])

        if len(string_to_convert) > max_size:
            raise ValueError(f"{string_to_convert} a plus de charactères qu'autorisés (max : {max_size})")

        return string_to_convert

    def _str_tochar(self, string_to_convert: str, params: list[str] = ["0"]) -> str:
        size = int(params[0])

        if len(string_to_convert) != size:
            raise ValueError(f"{string_to_convert} n'est pas de la bonne taille ({size})")

        return string_to_convert

    def _convert_to_null_value(self, type_name: str) -> str:
        null_value_dict = {"int": 0}
        default_null_value = " "

        return null_value_dict.get(type_name, default_null_value)


class _ToDisplay:
    def __init__(self, parent):
        self.parent: Convert = parent

    def transform(self, type_name: str, value_to_convert: str) -> str:
        if value_to_convert == "":
            return self._convert_to_null_value(type_name)
        elif self.func_dict().get(type_name):
            return self.func_dict()[type_name](value_to_convert)
        else:
            return value_to_convert

    def func_dict(self) -> dict:
        my_dict = {"date": self._date_to_str, "datetime": self._datetime_to_str}

        return my_dict

    def _date_to_str(self, value_to_convert: str) -> str:
        my_string = datetime.strptime(value_to_convert, self.parent.date_val_format).strftime(
            self.parent.date_txt_format
        )
        return my_string

    def _datetime_to_str(self, value_to_convert: str) -> str:
        my_string = datetime.strptime(value_to_convert, self.parent.datetime_val_format).strftime(
            self.parent.datetime_txt_format
        )
        return my_string

    def _convert_to_null_value(self, type_name: str) -> str:
        null_value_dict = {"int": 0}
        default_null_value = ""

        return null_value_dict.get(type_name, default_null_value)


class _FromResult:
    def __init__(self, parent):
        self.parent: Convert = parent

    def transform(self, value) -> str:
        type_name = str(type(value))

        if self.func_dict().get(type_name):
            return self.func_dict()[type_name](value)
        else:
            return str(value).replace(self.parent.field_separator, "")  # enlever txt identique au délim de champs

    def func_dict(self) -> dict:
        my_dict = {
            "<class 'str'>": self._from_string,
            "<class 'decimal.Decimal'>": self._from_number,
            "<class 'float'>": self._from_number,
            "<class 'int'>": self._from_number,
            "<class 'bool'>": self._from_bool,
            "<class 'datetime.datetime'>": self._from_datetime,
            "<class 'NoneType'>": self._from_none,
        }

        return my_dict

    def _from_string(self, value: str) -> str:
        if value == " ":
            value_txt = ""
        else:
            value_txt = value.replace(self.parent.field_separator, "")  # enlever txt identique au délim de champs

        return value_txt

    def _from_number(self, value) -> str:
        if str(value)[0:3] == "0E-":
            value_txt = ""
        else:
            value_txt = re.sub(r"(\.\d*?)(0*$)", r"\1", str(value))  # enleve trailing 0 des décimales
            if value_txt[-1] == ".":  # si dernier caractère séparateur décimal alors l'enlever
                value_txt = value_txt[:-1]
            else:  # sinon on le remplace par le séparateur décimal voulu
                value_txt = value_txt.replace(".", self.parent.decimal_separator)

        return value_txt

    def _from_bool(self, value: bool) -> str:
        if value:
            value_txt = "Vrai"
        else:
            value_txt = "Faux"

        return value_txt

    def _from_datetime(self, value: datetime) -> str:
        if str(value) == "1753-01-01 00:00:00":
            value_txt = ""
        elif value.strftime("%H:%M:%S") == "00:00:00":
            value_txt = value.strftime(self.parent.date_txt_format)
        else:
            value_txt = value.strftime(self.parent.datetime_txt_format)

        return value_txt

    def _from_none(self, _) -> str:
        return ""
