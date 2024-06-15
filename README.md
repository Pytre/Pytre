# Pytre

This is the official repository of Pytre.  
In French it stands for "**Pyt**hon **Re**queteur" and it's a pun. A "pitre" in french is someone being a clown, a buffoon.

Pytre is a query tool to allow users to run prepackaged SQL query on an Microsoft SQL Server database. Users do not need to know any SQL.

As it's developed and used for French users a lot of comments or explanations are in french.
It's a personal project and there is a lot of things I would want to do, like multi servers, improving code, making it multi languages, and so on.
Yet development is slow. I am working on it when I have time and as it's already good enough to what it is intended for it's not high priority !

I am not looking for contributions but if you have any questions I would be happy to answer them.

## Setup

Copy from templates "credentials_secrets.py" to Pytre folder and change key.  
Launch "\_\_main\_\_.py".

If no Pytre.db and Pytre.key were previously existing then the program will create them on first run.  
If no user is set as superuser / admin then current user will be given admin rights.

Folder containing queries files should also contains "\_version_min.json" from templates.  
It is used to make sure no one is using an old version when implementing new restrictions.

## License

Pytre is licensed under GNU Affero General Public License 3. You can find the license text in the LICENSE file.

## Used libraries

- [pymssql](https://github.com/pymssql/pymssql)
- [pykeepass](https://github.com/libkeepass/pykeepass)
- [cryptography](https://github.com/pyca/cryptography)

## Credits

- Icon from [FlatIcon.com](https://www.flaticon.com/free-icon/buffoon_688319)
