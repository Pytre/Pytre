/* Infos requête
Code : ZGL
Description : Grand-Livre
*/

DECLARE
@1 as int = 4,	-- Référentiel (1 pour social, 4 pour situ)
@2 as nvarchar(5) = 'GF521',	-- Regroup.Sté (optionnel)|1
@3 as nchar(5) = ' ',	-- Société Début (optionnel)|1
@4 as nchar(5) = ' ',	-- Société Fin (optionnel)|1
@5 as date = '2020-10-01',	-- Date Début|0|fiscal_year(9, -12, 1)
@6 as date = '2021-07-31',	-- Date Fin|0|month_end(-1)
@7 as nchar(7) = '4510000',	-- Compte Début (optionnel)|1
@8 as nchar(7) = '4559999',	-- Compte Fin (optionnel)|1
@9 as nvarchar(max) = ' ',	-- Tiers Début (optionnel)|1
@10 as nvarchar(max) = ' ',	-- Tiers Fin (optionnel)|1
@11 as nvarchar(10) = ' ',	-- Journal (optionnel)|1
@12 as int = 1	-- Ecritures entières (2 pour oui, 1 pour non)
;

SELECT
	DAE.CPY_0 as Societe,
	DAE.FCYLIN_0 as Site,
	DAE.ACCDAT_0 as Date_Cpta,
	HAE.JOU_0 as Journal,
	DAE.ACC_0 as Compte,
	DAE.BPR_0 as Tiers,
	DAE.OFFACC_0 as Contrepartie,
	DAE.DES_0 + ' / ' + HAE.DESVCR_0 + (CASE WHEN PIH.DES_0 IS NULL THEN '' ELSE ' / ' + PIH.DES_0 END) as Libelle,
	CASE	WHEN DAA.NUM_0 IS NULL AND DAE.SNS_0 = 1 THEN DAE.AMTLED_0
			WHEN DAA.SNS_0 = 1 THEN DAA.AMTLED_0
			ELSE 0
	END as Debit,
	CASE	WHEN DAA.NUM_0 IS NULL AND DAE.SNS_0 = -1 THEN DAE.AMTLED_0
			WHEN DAA.SNS_0 = -1 THEN DAA.AMTLED_0
			ELSE 0
	END as Credit,
	DAE.CUR_0 as Devise,
	DAE.NUM_0 as Num_Piece,
	DAE.TYP_0 as Type_Piece,
	HAE.BPRVCR_0 as Doc_Origine,
	Info_Presta.STRDAT as Presta_Debut,
	Info_Presta.ENDDAT as Presta_Fin,
	DAA.CCE_0 as Section_Ana_Service,
	DAA.CCE_1 as Section_Ana_BU,
	DAA.CCE_2 as Section_Ana_Enseigne,
	DAA.CCE_3 as Section_Ana_Nature,
	DAA.CCE_4 as Section_Ana_Projet,
	coalesce(BPR_Tiers.Id_Conso, BPR_Contrepartie.Id_Conso) as Id_Interco_Conso,
	(CASE
		WHEN coalesce(BPR_Tiers.Id_Conso, BPR_Contrepartie.Id_Conso) IS NULL THEN ''
		WHEN HAE.CPY_0 < coalesce(BPR_Tiers.Id_Conso, BPR_Contrepartie.Id_Conso) THEN HAE.CPY_0 + '-' + coalesce(BPR_Tiers.Id_Conso, BPR_Contrepartie.Id_Conso)
		ELSE coalesce(BPR_Tiers.Id_Conso, BPR_Contrepartie.Id_Conso) + '-' + HAE.CPY_0
	END) as Couple_Conso,
	PIH.YSCAN_0 as Lien_Alusta
FROM
	x3v12prod.PROSOL2.GACCENTRY HAE
	INNER JOIN x3v12prod.PROSOL2.GACCENTRYD DAE ON HAE.TYP_0 = DAE.TYP_0 AND HAE.NUM_0 = DAE.NUM_0 AND DAE.LEDTYP_0 = @1
	LEFT OUTER JOIN x3v12prod.PROSOL2.GACCENTRYA DAA ON HAE.TYP_0 = DAA.TYP_0 AND HAE.NUM_0 = DAA.NUM_0 AND DAE.LIN_0 = DAA.LIN_0
	LEFT OUTER JOIN x3v12prod.PROSOL2.FACGROUP FGR ON FGR.FCY_0 = HAE.FCY_0
	LEFT OUTER JOIN x3v12prod.PROSOL2.PINVOICE PIH ON PIH.NUM_0 = HAE.NUM_0
	LEFT OUTER JOIN (
		SELECT
			PIL.NUM_0 as NUM, PIL.ACC_0 as ACC,
			SUBSTRING(SUBSTRING(PIH_TMP.BPRNAM_0, 1, 10) + ' ' + PIL.DES_0, 1, 30) as DES,
			PIL.AMTNOTLIN_0 as AMTNOTLIN,
			MIN(PIL.STRDAT_0) as STRDAT, MAX(PIL.ENDDAT_0) as ENDDAT
		FROM x3v12prod.PROSOL2.PINVOICE PIH_TMP LEFT OUTER JOIN x3v12prod.PROSOL2.BPSINVLIG PIL ON PIL.NUM_0 = PIH_TMP.NUM_0
		WHERE PIL.STRDAT_0 >= '1901-01-01'
		GROUP BY PIL.NUM_0, PIL.ACC_0, SUBSTRING(SUBSTRING(PIH_TMP.BPRNAM_0, 1, 10) + ' ' + PIL.DES_0, 1, 30), PIL.AMTNOTLIN_0
	) as Info_Presta ON Info_Presta.NUM = PIH.NUM_0 AND Info_Presta.ACC = DAE.ACC_0 AND Info_Presta.AMTNOTLIN = DAE.AMTLED_0 * DAE.SNS_0 AND Info_Presta.DES = DAE.DES_0
	LEFT OUTER JOIN x3v12prod.PROSOL2.ZSOCCONSO SOC ON SOC.SOCIETE_0 = HAE.CPY_0
	LEFT OUTER JOIN (
		SELECT BPR.BPRNUM_0 as Tiers, SOC.CONSO_0 as Id_Conso
		FROM x3v12prod.PROSOL2.BPARTNER BPR LEFT OUTER JOIN x3v12prod.PROSOL2.ZSOCCONSO SOC on SOC.SITE_0 = BPR.GRUCOD_0
		WHERE BPR.GRUCOD_0 <> ' '
	) as BPR_Tiers ON BPR_Tiers.Tiers = DAE.BPR_0 AND DAE.BPR_0 <> ' '
	LEFT OUTER JOIN (
		SELECT BPR.BPRNUM_0 as Tiers, SOC.CONSO_0 as Id_Conso
		FROM x3v12prod.PROSOL2.BPARTNER BPR LEFT OUTER JOIN x3v12prod.PROSOL2.ZSOCCONSO SOC on SOC.SITE_0 = BPR.GRUCOD_0
		WHERE BPR.GRUCOD_0 <> ' '
	) as BPR_Contrepartie ON BPR_Contrepartie.Tiers = DAE.OFFACC_0 AND DAE.OFFACC_0 <> ' ' AND SUBSTRING(DAE.ACC_0, 1, 1) IN ('6', '7')
WHERE
	(FGR.CPY_0 = @2 OR (@2 = ' ' AND FGR.CPY_0 = DAE.CPY_0))
	AND (DAE.CPY_0 >= @3 OR @3 = ' ') AND (DAE.CPY_0 <= @4 OR @4 = ' ')
	AND DAE.ACCDAT_0 >= @5 AND DAE.ACCDAT_0 <= @6
	AND DAE.ACC_0 <> ' '
	AND (HAE.JOU_0 = @11 OR @11 = ' ')
	AND (
				(@12 = 1
				AND (DAE.ACC_0 >= @7 OR @7 = ' ') AND (DAE.ACC_0 <= @8 OR @8 = ' ')
				AND (DAE.BPR_0 >= @9 OR @9 = ' ') AND (DAE.BPR_0 <= @10 OR @10 = ' ')
				)
			OR	(@12 = 2 AND HAE.NUM_0 IN (
				SELECT DISTINCT
					HAE.NUM_0
				FROM
					x3v12prod.PROSOL2.GACCENTRY HAE
					INNER JOIN x3v12prod.PROSOL2.GACCENTRYD DAE ON DAE.TYP_0 = HAE.TYP_0 AND HAE.NUM_0 = DAE.NUM_0 AND DAE.LEDTYP_0 = @1
					LEFT OUTER JOIN x3v12prod.PROSOL2.FACGROUP FGR ON FGR.FCY_0 = HAE.FCY_0
				WHERE
					(FGR.CPY_0 = @2 OR (@2 = ' ' AND FGR.CPY_0 = DAE.CPY_0))
					AND (DAE.CPY_0 >= @3 OR @3 = ' ') AND (DAE.CPY_0 <= @4 OR @4 = ' ')
					AND HAE.ACCDAT_0 >= @5 AND HAE.ACCDAT_0 <= @6
					AND(DAE.ACC_0 >= @7 OR @7 = ' ') AND (DAE.ACC_0 <= @8 OR @8 = ' ')
					AND DAE.ACC_0 <> ' '
					AND (DAE.BPR_0 >= @9 OR @9 = ' ') AND (DAE.BPR_0 <= @10 OR @10 = ' ')
					AND (HAE.JOU_0 = @11 OR @11 = ' ')
				))
		)
