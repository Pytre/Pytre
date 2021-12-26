/* Infos requête
Code : ZIMMOCTR
Description : Controle  X3 Cpta vs X3 Immos
*/

DECLARE
@1 as date = '2021-10-01',	-- Date début|0|fiscal_year(9, -12, 1)
@2 as date = '2021-12-31',	-- Date fin|0|month_end(-1)
@3 as nvarchar(5) = ' ',	-- Regroup.Sté (facultatif)|1
@4 as nchar(5) = ' ',	-- Société début (facultatif)|1
@5 as nchar(5) = ' ',	-- Société fin (facultatif)|1
@6 as int = 1,	-- Ecarts uniquement ? (2 pour oui et 1 pour non)|0
@7 as nvarchar(10) = 'FRA',	-- Plan de compte|0
@8 as int = 1	-- Référentiel (1 pour social, 4 pour situ)|0
;

SELECT
		Societe, Site, Compte,
		SUM(Montant_Cpta) as Montant_Cpta,
		SUM(Montant_Immos) as Montant_Immos,
		SUM(Montant_Cpta - Montant_Immos) as Cpta_Vs_Immos
FROM (
	/************************/
	/* RECUP SELON X3 IMMOS */
	/************************/
		SELECT /* récup montant immos fin attendu */
			F.CPY_0 as Societe,	F.FCY_0 as Site,
			C.ACC_0 as Compte,
			0 as Montant_Cpta,
			(CASE WHEN F.ISSDAT_0 <= @2 AND F.ISSDAT_0 >= '1901-01-01' THEN 0 ELSE F.ACGETRNOT_0 END) as Montant_Immos
		FROM
			x3v12prod.PROSOL2.FXDASSETS F 
			INNER JOIN x3v12prod.PROSOL2.DEPREC D ON D.DPRPLN_0 = 1 AND F.AASREF_0 = D.AASREF_0 AND D.PERENDDAT_0 = @2
			INNER JOIN x3v12prod.PROSOL2.GACCCODE C ON F.ACCCOD_0 = C.ACCCOD_0 AND C.COA_0 = @7
			LEFT OUTER JOIN x3v12prod.PROSOL2.FACGROUP FGR ON FGR.FCY_0 = F.FCY_0
		WHERE
			(FGR.CPY_0 = @3 OR (@3 = ' ' AND FGR.CPY_0 = F.CPY_0))
			AND (F.CPY_0 >= @4 OR @4 = ' ') AND (F.CPY_0 <= @5 OR @5 = ' ')
	UNION ALL
		SELECT /* récup montant amort fin attendu */
			F.CPY_0 as Societe,	F.FCY_0 as Site,
			C.ACC_1 as Compte,
			0 as Montant_Cpta,
			(CASE WHEN F.ISSDAT_0 <= @2 AND F.ISSDAT_0 >= '1901-01-01' THEN 0 ELSE -(D.DPRCUM_0 + D.PERCLOCUM_0 + D.PERENDDPE_0) END) as Montant_Immos
		FROM
			x3v12prod.PROSOL2.FXDASSETS F 
			INNER JOIN x3v12prod.PROSOL2.DEPREC D ON D.DPRPLN_0 = 1 AND F.AASREF_0 = D.AASREF_0 AND D.PERENDDAT_0 = @2
			INNER JOIN x3v12prod.PROSOL2.GACCCODE C ON F.ACCCOD_0 = C.ACCCOD_0 AND C.COA_0 = @7
			LEFT OUTER JOIN x3v12prod.PROSOL2.FACGROUP FGR ON FGR.FCY_0 = F.FCY_0
		WHERE
			(FGR.CPY_0 = @3 OR (@3 = ' ' AND FGR.CPY_0 = F.CPY_0))
			AND (F.CPY_0 >= @4 OR @4 = ' ') AND (F.CPY_0 <= @5 OR @5 = ' ')
	UNION ALL
		SELECT /* récup montant amort dérogatoire attendu */
			F.CPY_0 as Societe,	F.FCY_0 as Site,
			C.ACC_5 as Compte,
			0 as Montant_Cpta,
			(CASE WHEN F.ISSDAT_0 <= @2 AND F.ISSDAT_0 >= '1901-01-01' THEN 0 ELSE -(D.LEGCUM_0 - D.LEGRVECUM_0 + D.PERLEGCUM_0 + D.LEG_0 - D.PERLEGRVE_0 - D.LEGRVE_0) END) as Montant_Immos
		FROM
			x3v12prod.PROSOL2.FXDASSETS F 
			INNER JOIN x3v12prod.PROSOL2.DEPREC D ON D.DPRPLN_0 = 1 AND F.AASREF_0 = D.AASREF_0 AND D.PERENDDAT_0 = @2
			INNER JOIN x3v12prod.PROSOL2.GACCCODE C ON F.ACCCOD_0 = C.ACCCOD_0 AND C.COA_0 = @7
			LEFT OUTER JOIN x3v12prod.PROSOL2.FACGROUP FGR ON FGR.FCY_0 = F.FCY_0
		WHERE
			(FGR.CPY_0 = @3 OR (@3 = ' ' AND FGR.CPY_0 = F.CPY_0))
			AND (F.CPY_0 >= @4 OR @4 = ' ') AND (F.CPY_0 <= @5 OR @5 = ' ')
	UNION ALL
		SELECT /* récup montant dotation attendue - 1 : Cumul amort N */
			F.CPY_0 as Societe,	F.FCY_0 as Site,
			C.ACC_2 as Compte,
			0 as Montant_Cpta,
			(CASE WHEN F.ISSDAT_0 <= DATEADD(day, -1, @1) AND F.ISSDAT_0 >= '1901-01-01' THEN 0 ELSE D.DPRCUM_0 + D.PERCLOCUM_0 + D.PERENDDPE_0 END) as Montant_Immos
		FROM
			x3v12prod.PROSOL2.FXDASSETS F 
			INNER JOIN x3v12prod.PROSOL2.DEPREC D ON D.DPRPLN_0 = 1 AND F.AASREF_0 = D.AASREF_0 AND D.PERENDDAT_0 = @2
			INNER JOIN x3v12prod.PROSOL2.GACCCODE C ON F.ACCCOD_0 = C.ACCCOD_0 AND C.COA_0 = @7
			LEFT OUTER JOIN x3v12prod.PROSOL2.FACGROUP FGR ON FGR.FCY_0 = F.FCY_0
		WHERE
			(FGR.CPY_0 = @3 OR (@3 = ' ' AND FGR.CPY_0 = F.CPY_0))
			AND (F.CPY_0 >= @4 OR @4 = ' ') AND (F.CPY_0 <= @5 OR @5 = ' ')
	UNION ALL
		SELECT /* récup montant dotation attendue - 2 : Neutral Cumul amort N-1 */
			F.CPY_0 as Societe,	F.FCY_0 as Site,
			C.ACC_2 as Compte,
			0 as Montant_Cpta,
			(CASE WHEN F.ISSDAT_0 <= DATEADD(day, -1, @1) AND F.ISSDAT_0 >= '1901-01-01' THEN 0 ELSE -(D.DPRCUM_0 + D.PERCLOCUM_0 + D.PERENDDPE_0) END) as Montant_Immos
		FROM
			x3v12prod.PROSOL2.FXDASSETS F 
			INNER JOIN x3v12prod.PROSOL2.DEPREC D ON D.DPRPLN_0 = 1 AND F.AASREF_0 = D.AASREF_0 AND D.PERENDDAT_0 = DATEADD(day, -1, @1)
			INNER JOIN x3v12prod.PROSOL2.GACCCODE C ON F.ACCCOD_0 = C.ACCCOD_0 AND C.COA_0 = @7
			LEFT OUTER JOIN x3v12prod.PROSOL2.FACGROUP FGR ON FGR.FCY_0 = F.FCY_0
		WHERE
			(FGR.CPY_0 = @3 OR (@3 = ' ' AND FGR.CPY_0 = F.CPY_0))
			AND (F.CPY_0 >= @4 OR @4 = ' ') AND (F.CPY_0 <= @5 OR @5 = ' ')
	UNION ALL
		SELECT /* récup montant 675 VNC attendue */
			F.CPY_0 as Societe,	F.FCY_0 as Site,
			C.ACC_9 as Compte,
			0 as Montant_Cpta,
			(CASE WHEN F.ISSDAT_0 <= @2 AND F.ISSDAT_0 >= @1 THEN F.ACGETRNOT_0 - D.DPRCUM_0 - D.PERCLOCUM_0 - D.PERENDDPE_0 ELSE 0 END) as Montant_Immos
		FROM
			x3v12prod.PROSOL2.FXDASSETS F 
			INNER JOIN x3v12prod.PROSOL2.DEPREC D ON D.DPRPLN_0 = 1 AND F.AASREF_0 = D.AASREF_0 AND D.PERENDDAT_0 = @2
			INNER JOIN x3v12prod.PROSOL2.GACCCODE C ON F.ACCCOD_0 = C.ACCCOD_0 AND C.COA_0 = @7
			LEFT OUTER JOIN x3v12prod.PROSOL2.FACGROUP FGR ON FGR.FCY_0 = F.FCY_0
		WHERE
			(FGR.CPY_0 = @3 OR (@3 = ' ' AND FGR.CPY_0 = F.CPY_0))
			AND (F.CPY_0 >= @4 OR @4 = ' ') AND (F.CPY_0 <= @5 OR @5 = ' ')
			AND CAST(C.ACC_0 as VARCHAR) NOT LIKE '275%'
	UNION ALL
		SELECT /* récup montant pdts de cessions */
			F.CPY_0 as Societe,	F.FCY_0 as Site,
			C.ACC_10 as Compte,
			0 as Montant_Cpta,
			/* -F.ISSAMT_0 as Montant_Immos */
			(CASE WHEN F.ISSDAT_0 <= @2 AND F.ISSDAT_0 >= @1 THEN -F.ISSAMT_0 ELSE 0 END) as Montant_Immos
		FROM
			x3v12prod.PROSOL2.FXDASSETS F 
			INNER JOIN x3v12prod.PROSOL2.DEPREC D ON D.DPRPLN_0 = 1 AND F.AASREF_0 = D.AASREF_0 AND D.PERENDDAT_0 = @2
			INNER JOIN x3v12prod.PROSOL2.GACCCODE C ON F.ACCCOD_0 = C.ACCCOD_0 AND C.COA_0 = @7
			LEFT OUTER JOIN x3v12prod.PROSOL2.FACGROUP FGR ON FGR.FCY_0 = F.FCY_0
		WHERE
			(FGR.CPY_0 = @3 OR (@3 = ' ' AND FGR.CPY_0 = F.CPY_0))
			AND (F.CPY_0 >= @4 OR @4 = ' ') AND (F.CPY_0 <= @5 OR @5 = ' ')
			AND CAST(C.ACC_0 as VARCHAR) NOT LIKE '275%'
	UNION ALL
		SELECT /* récup montant dotation dérogatoire */
			F.CPY_0 as Societe,	F.FCY_0 as Site,
			C.ACC_4 as Compte,
			0 as Montant_Cpta,
			D.PERLEGCUM_0 + D.LEG_0 as Montant_Immos
		FROM
			x3v12prod.PROSOL2.FXDASSETS F 
			INNER JOIN x3v12prod.PROSOL2.DEPREC D ON D.DPRPLN_0 = 1 AND F.AASREF_0 = D.AASREF_0 AND D.PERENDDAT_0 = @2
			INNER JOIN x3v12prod.PROSOL2.GACCCODE C ON F.ACCCOD_0 = C.ACCCOD_0 AND C.COA_0 = @7
			LEFT OUTER JOIN x3v12prod.PROSOL2.FACGROUP FGR ON FGR.FCY_0 = F.FCY_0
		WHERE
			(FGR.CPY_0 = @3 OR (@3 = ' ' AND FGR.CPY_0 = F.CPY_0))
			AND (F.CPY_0 >= @4 OR @4 = ' ') AND (F.CPY_0 <= @5 OR @5 = ' ')
	UNION ALL
		SELECT /* récup montant reprise dérogatoire */
			F.CPY_0 as Societe,	F.FCY_0 as Site,
			C.ACC_8 as Compte,
			0 as Montant_Cpta,
			-(D.PERLEGRVE_0 + D.LEGRVE_0 + (CASE WHEN F.ISSDAT_0 <= @2 AND F.ISSDAT_0 >= @1 THEN F.DERRVEISS_0 ELSE 0 END)) as Montant_Immos
		FROM
			x3v12prod.PROSOL2.FXDASSETS F 
			INNER JOIN x3v12prod.PROSOL2.DEPREC D ON D.DPRPLN_0 = 1 AND F.AASREF_0 = D.AASREF_0 AND D.PERENDDAT_0 = @2
			INNER JOIN x3v12prod.PROSOL2.GACCCODE C ON F.ACCCOD_0 = C.ACCCOD_0 AND C.COA_0 = @7
			LEFT OUTER JOIN x3v12prod.PROSOL2.FACGROUP FGR ON FGR.FCY_0 = F.FCY_0
		WHERE
			(FGR.CPY_0 = @3 OR (@3 = ' ' AND FGR.CPY_0 = F.CPY_0))
			AND (F.CPY_0 >= @4 OR @4 = ' ') AND (F.CPY_0 <= @5 OR @5 = ' ')
	/*************************/
	/* RECUP SELON X3 COMPTA */
	/*************************/
	UNION ALL
		SELECT
			HAE.CPY_0 as Societe, HAE.FCY_0 as Site,
			DAE.ACC_0 as Compte,
			(DAE.AMTLED_0 * DAE.SNS_0) as Montant_Cpta,
			0 as Montant_Immos
		FROM
			x3v12prod.PROSOL2.GACCENTRY HAE
			INNER JOIN x3v12prod.PROSOL2.GACCENTRYD DAE ON HAE.TYP_0 = DAE.TYP_0 AND HAE.NUM_0 = DAE.NUM_0 AND DAE.LEDTYP_0 = @8
			LEFT OUTER JOIN x3v12prod.PROSOL2.FACGROUP FGR ON FGR.FCY_0 = HAE.FCY_0
		WHERE
			(FGR.CPY_0 = @3 OR (@3 = ' ' AND FGR.CPY_0 = HAE.CPY_0))
			AND (HAE.CPY_0 >= @4 OR @4 = ' ') AND (HAE.CPY_0 <= @5 OR @5 = ' ')
			AND (	CAST(DAE.ACC_0 as VARCHAR) LIKE '145%'
					OR (CAST(DAE.ACC_0 as VARCHAR) LIKE '2%'	AND NOT CAST(DAE.ACC_0 as VARCHAR) LIKE '267%'
																AND NOT CAST(DAE.ACC_0 as VARCHAR) LIKE '276%'
																AND NOT CAST(DAE.ACC_0 as VARCHAR) LIKE '29%')
					OR CAST(DAE.ACC_0 as VARCHAR) LIKE '6811%'  OR CAST(DAE.ACC_0 as VARCHAR) LIKE '7811%'
					OR CAST(DAE.ACC_0 as VARCHAR) LIKE '68725%' OR CAST(DAE.ACC_0 as VARCHAR) LIKE '78725%'
					OR CAST(DAE.ACC_0 as VARCHAR) LIKE '675%' OR CAST(DAE.ACC_0 as VARCHAR) LIKE '775%')
			AND HAE.ACCDAT_0 >= @1 AND HAE.ACCDAT_0 <= @2
) as Fact_Data
WHERE Montant_Cpta <> 0 OR Montant_Immos <> 0
GROUP BY Societe, Site, Compte
HAVING
	(NOT Compte LIKE '23%' AND (SUM(Montant_Cpta - Montant_Immos) <> 0 OR @6 = 1))
	OR (Compte LIKE '23%' AND SUM(Montant_Cpta) < 0)
ORDER BY Societe, Site, Compte
