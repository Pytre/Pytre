/* Infos requête
Code : ZBALAGE
Description : Balance agée
*/

DECLARE
@1 as int = 1,	-- Référentiel (1 pour social, 4 pour situ)
@2 as date = '2021-09-30',	-- Date Situ|0|month_end(-1)
@3 as int = 24,	-- Nb Mois avec Détail
@4 as nvarchar(5) = 'GF521',	-- Regroup.Sté (optionnel)|1
@5 as nchar(5) = ' ',	-- Société début (optionnel)|1
@6 as nchar(5) = ' ',	-- Société fin (optionnel)|1
@7 as nchar(7) = '4010000',	-- Compte début
@8 as nchar(7) = '4049999'	-- Compte fin
;

WITH Tbl_Solde AS (
	SELECT
		HAE.CPY_0 as Societe,
		DAE.ACC_0 as Cpte_Gen,
		DAE.BPR_0 as Cpte_Aux,
		SUM(DAE.AMTLED_0 * DAE.SNS_0) as Montant
	FROM
		x3v12prod.PROSOL2.GACCENTRYD DAE
		LEFT OUTER JOIN
			x3v12prod.PROSOL2.GACCENTRY HAE ON HAE.TYP_0=DAE.TYP_0 AND HAE.NUM_0=DAE.NUM_0 AND DAE.LEDTYP_0 = @1
		LEFT OUTER JOIN
			x3v12prod.PROSOL2.FACGROUP FGR ON FGR.FCY_0 = HAE.FCY_0
	WHERE
		(FGR.CPY_0 = @4 OR (@4 = ' ' AND FGR.CPY_0 = HAE.CPY_0))
		AND (HAE.CPY_0 >= @5 OR @5 = ' ') AND (HAE.CPY_0 <= @6 OR @6 = ' ')
		AND DAE.ACC_0 >= @7 AND DAE.ACC_0 <= @8
		AND HAE.ACCDAT_0 <= @2
		AND HAE.FLGREP_0 <> 2 /* pour exclure les lignes de report à nouveau */
	GROUP BY
		HAE.CPY_0, DAE.ACC_0, DAE.BPR_0
),
Tbl_BalAge AS (
	SELECT
		HAE.CPY_0 as Societe,
		DAE.ACC_0 as Cpte_Gen,
		DAE.BPR_0 as Cpte_Aux,
		HAE.ACCDAT_0 as Date_Piece,
		HAE.TYP_0 as Type_Piece,
		HAE.NUM_0 as Num_Piece,
		DAE.DES_0 as Lib_Ligne,
		DUD.DUDDAT_0 as Date_Echeance,
		DUD.FLGPAZ_0 as Statut_Piece,
		DAE.MTC_0 as Lettrage,
		DUD.AMTLOC_0 * DUD.SNS_0 as Montant
	FROM
		x3v12prod.PROSOL2.GACCDUDATE DUD
		LEFT OUTER JOIN
			x3v12prod.PROSOL2.GACCENTRYD DAE ON DAE.TYP_0=DUD.TYP_0 AND DAE.NUM_0=DUD.NUM_0 AND DAE.LIN_0=DUD.LIG_0 AND DAE.LEDTYP_0 = @1
		LEFT OUTER JOIN
			x3v12prod.PROSOL2.GACCENTRY HAE ON HAE.TYP_0=DAE.TYP_0 AND HAE.NUM_0=DAE.NUM_0
		LEFT OUTER JOIN
			x3v12prod.PROSOL2.FACGROUP FGR ON FGR.FCY_0 = HAE.FCY_0
	WHERE
		(FGR.CPY_0 = @4 OR (@4 = ' ' AND FGR.CPY_0 = HAE.CPY_0))
		AND (HAE.CPY_0 >= @5 OR @5 = ' ') AND (HAE.CPY_0 <= @6 OR @6 = ' ')
		AND DAE.ACC_0 >= @7 AND DAE.ACC_0 <= @8
		AND HAE.ACCDAT_0 <= @2 AND HAE.ACCDAT_0 > DATEADD(month, @3 * -1, @2)
		AND (DAE.MTCDATMAX_0 > @2
			or DAE.MTCDATMAX_0 < '1-Jan-1901'
			or upper(DAE.MTC_0) <> DAE.MTC_0)
		AND HAE.FLGREP_0 <> 2 /* pour exclure les lignes de report à nouveau */
),
Tbl_Sans_Detail AS (
	SELECT
		Societe,
		Cpte_Gen,
		Cpte_Aux,
		DATEADD(month, @3 * -1, @2) as Date_Piece,
		'n/d' as Type_Piece,
		'n/d' as Num_Piece,
		'n/d' as Lib_Ligne,
		DATEADD(month, @3 * -1, @2) as Date_Echeance,
		0 as Statut_Piece,
		'' as Lettrage,
		SUM(Montant) as Montant
	FROM (
		SELECT Societe, Cpte_Gen, Cpte_Aux, Montant FROM Tbl_Solde
		UNION ALL SELECT Societe, Cpte_Gen, Cpte_Aux, Montant * -1 as Montant FROM Tbl_BalAge
	) as Tbl_Sans_Detail_Src
	GROUP BY Societe, Cpte_Gen, Cpte_Aux
)

SELECT
	Societe, Cpte_Gen, Cpte_Aux, Date_Piece, Type_Piece, Num_Piece, Lib_Ligne, Date_Echeance,
	cast(Statut_Piece as varchar) + ' - ' + coalesce(AST.LANMES_0, 'Inconnu') as Statut_Piece,
	Lettrage, Montant,
	Date_Piece - DAY(Date_Piece) + 1 as Mois_Piece,
	1 + YEAR(@2) * 12 + MONTH(@2) - YEAR(Date_Piece) * 12 - MONTH(Date_Piece) as Anteriorite_Piece,
	1 + Date_Echeance - DAY(Date_Echeance) as Mois_Echeance,
	DATEDIFF(day, cast(Date_Echeance as date), @2) as Nb_J_Avant_Echeance
FROM
	(
		SELECT * FROM Tbl_BalAge
		UNION ALL SELECT * FROM Tbl_Sans_Detail
	) as Tbl_Src
	LEFT OUTER JOIN x3v12prod.PROSOL2.APLSTD AST ON AST.LANNUM_0 = Tbl_Src.Statut_Piece AND AST.LANCHP_0 = 510 AND AST.LAN_0 = 'FRA' /* menu des types bon à payer */
WHERE
	Montant <> 0
ORDER BY
	Societe, Cpte_Gen, Cpte_Aux, Date_Piece
