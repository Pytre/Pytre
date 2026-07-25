/* Infos requête
Code : TEST_QUERY
Description : requête synthétique pour les tests d'intégration, sans dépendance au schéma métier réel
Hide : 0
*/

DECLARE
@1 as date = ' '	-- Date de filtre (optionnel)|optional
;

SELECT
	1 as id,
	'test' as label,
	@1 as filter_date
