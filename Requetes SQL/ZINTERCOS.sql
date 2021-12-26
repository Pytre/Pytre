/* Infos requête
Code : ZINTERCOS
Description : Contrôle des intercos entre 2 stés
*/

DECLARE
@1 as int = 1,	-- Référentiel (1 pour social, 4 pour situ)
@2 as nchar(5) = 'ZHGFI',	-- Société 1
@3 as nchar(5) = 'ZA002',	-- Société 2
@4 as date = '2020-10-01',	-- Date Début|0|fiscal_year(9, -12, 1)
@5 as date = '2021-09-30',	-- Date Fin|0|month_end(-1)
@6 as nchar(7) = '4010000',	-- Compte Début (optionnel)
@7 as nchar(7) = '4199999',	-- Compte Fin (optionnel)
@8 as int = 1	-- Uniquement date avec écarts (1 pour oui, 0 pour non)
;

/*
WITH Date_avec_Ecart as (
	SELECT DISTINCT DAE.ACCDAT_0 as Date_Cpta
	FROM
		x3v12prod.PROSOL2.GACCENTRYD DAE
		LEFT OUTER JOIN (SELECT BPR.BPRNUM_0, SOC.CONSO_0 FROM x3v12prod.PROSOL2.ZSOCCONSO SOC LEFT OUTER JOIN x3v12prod.PROSOL2.BPARTNER as BPR ON BPR.GRUCOD_0 = SOC.SITE_0) as SOC_Tiers ON SOC_Tiers.BPRNUM_0 = DAE.BPR_0 AND DAE.BPR_0 <> ' '
		LEFT OUTER JOIN (SELECT BPR.BPRNUM_0, SOC.CONSO_0 FROM x3v12prod.PROSOL2.ZSOCCONSO SOC LEFT OUTER JOIN x3v12prod.PROSOL2.BPARTNER as BPR ON BPR.GRUCOD_0 = SOC.SITE_0) as SOC_Contrepartie ON SOC_Contrepartie.BPRNUM_0 = DAE.OFFACC_0 AND DAE.OFFACC_0 <> ' ' AND SUBSTRING(DAE.ACC_0, 1, 1) IN ('6', '7')
	WHERE
		DAE.LEDTYP_0 = @1
		AND (DAE.CPY_0 = @2 OR DAE.CPY_0 = @3)
		AND DAE.ACCDAT_0 >= @4 AND DAE.ACCDAT_0 <= @5
		AND DAE.ACC_0 <> ' ' AND (DAE.ACC_0 >= @6 OR @6 = ' ') AND (DAE.ACC_0 <= @7 OR @7 = ' ')
		AND coalesce(SOC_Tiers.CONSO_0, SOC_Contrepartie.CONSO_0) IN (@2, @3)
	GROUP BY DAE.ACCDAT_0, CASE WHEN SUBSTRING(DAE.ACC_0, 1, 1) < '6' THEN 1 ELSE 0 END
	HAVING SUM(DAE.AMTLED_0 * DAE.SNS_0) <> 0
)
*/

SELECT
	DAE.CPY_0 as Societe,
	DAE.FCYLIN_0 as Site,
	DAE.ACCDAT_0 as Date_Cpta,
	HAE.JOU_0 as Journal,
	DAE.ACC_0 as Compte,
	DAE.BPR_0 as Tiers,
	DAE.OFFACC_0 as Contrepartie,
	DAE.DES_0 + ' / ' + HAE.DESVCR_0 as Libelle,
	DAE.AMTLED_0 * DAE.SNS_0 as Montant,
	DAE.CUR_0 as Devise,
	DAE.NUM_0 as Num_Piece,
	DAE.TYP_0 as Type_Piece,
	HAE.BPRVCR_0 as Doc_Origine,
	coalesce(SOC_Tiers.CONSO_0, SOC_Contrepartie.CONSO_0) as Id_Interco_Conso,
	(CASE
		WHEN coalesce(SOC_Tiers.CONSO_0, SOC_Contrepartie.CONSO_0) IS NULL THEN ''
		WHEN DAE.CPY_0 < coalesce(SOC_Tiers.CONSO_0, SOC_Contrepartie.CONSO_0) THEN DAE.CPY_0 + '-' + coalesce(SOC_Tiers.CONSO_0, SOC_Contrepartie.CONSO_0)
		ELSE coalesce(SOC_Tiers.CONSO_0, SOC_Contrepartie.CONSO_0) + '-' + DAE.CPY_0
	END) as Couple_Conso,
	SOC_Contrepartie.CONSO_0

FROM
	x3v12prod.PROSOL2.GACCENTRYD DAE
	-- INNER JOIN Date_avec_Ecart ON Date_avec_Ecart.Date_Cpta = DAE.ACCDAT_0
	INNER JOIN x3v12prod.PROSOL2.GACCENTRY HAE ON HAE.TYP_0 = DAE.TYP_0 AND HAE.NUM_0 = DAE.NUM_0
	LEFT OUTER JOIN (SELECT BPR.BPRNUM_0, SOC.CONSO_0 FROM x3v12prod.PROSOL2.BPARTNER as BPR LEFT OUTER JOIN x3v12prod.PROSOL2.ZSOCCONSO SOC ON BPR.GRUCOD_0 = SOC.SITE_0) as SOC_Tiers ON SOC_Tiers.BPRNUM_0 = DAE.BPR_0 AND DAE.BPR_0 <> ' '
	LEFT OUTER JOIN (SELECT BPR.BPRNUM_0, COALESCE(SOC.CONSO_0, BPR.GRUCOD_0) as CONSO_0 FROM x3v12prod.PROSOL2.BPARTNER as BPR LEFT OUTER JOIN x3v12prod.PROSOL2.ZSOCCONSO SOC ON BPR.GRUCOD_0 = SOC.SITE_0) as SOC_Contrepartie ON SOC_Contrepartie.BPRNUM_0 = DAE.OFFACC_0 AND DAE.OFFACC_0 <> ' ' AND SUBSTRING(DAE.ACC_0, 1, 1) IN ('6', '7')
WHERE
	DAE.LEDTYP_0 = @1
	AND (DAE.CPY_0 = @2 OR DAE.CPY_0 = @3)
	AND DAE.ACCDAT_0 >= @4 AND DAE.ACCDAT_0 <= @5
	AND DAE.ACC_0 <> ' ' AND (DAE.ACC_0 >= @6 OR @6 = ' ') AND (DAE.ACC_0 <= @7 OR @7 = ' ')
	AND coalesce(SOC_Tiers.CONSO_0, SOC_Contrepartie.CONSO_0) IN (@2, @3)
ORDER BY
	DAE.ACCDAT_0, DAE.AMTLED_0
