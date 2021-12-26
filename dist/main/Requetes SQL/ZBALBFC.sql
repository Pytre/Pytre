/* Infos requête
Code : ZBALBFC
Description : Balance conso
*/

DECLARE
@1 as int = 4,	-- Référentiel (1 pour social, 4 pour situ)
@2 as nvarchar(5) = ' ',	-- Regroup.Sté (optionnel)|1
@3 as nchar(5) = ' ',	-- Société Début (optionnel)|1
@4 as nchar(5) = ' ',	-- Société Fin (optionnel)|1
@5 as date = '2020-10-01',	-- Date Début|0|fiscal_year(9, -12, 1)
@6 as date = '2021-09-30',	-- Date Fin|0|month_end(-1)
@7 as nchar(7) = ' ',	-- Compte Début (optionnel)|1
@8 as nchar(7) = ' ',	-- Compte Fin (optionnel)|1
@9 as int = 1	-- Sans RAN > Date Début (1 pour oui, 0 pour non)
;

SELECT
	convert(varchar, year(@6)) + '.' + right('0'+convert(varchar,month(@6)),2) as PERIODE,
	SC1.CONSO_0 as SOCIETE,
	Site as SITE,
	Compte as COMPTE,
	Lib_Cpte as [LIBELLE COMPTE],
	coalesce(SC2.CONSO_0, BPR.GRUCOD_0) as INTERCO,
	Lib_Tiers as [LIBELLE TIERS],
	Tiers as [COMPTE TIERS],
	SUM(Montant) as MONTANT
FROM
	(	SELECT /* Requête pour obtenir les données N */
			DAE.CPY_0 as Societe,
			DAE.FCYLIN_0 as Site,
			DAE.ACC_0 as Compte,
			(CASE 
				WHEN GAC.CSLFLG_0 <> 2 THEN ' '
				WHEN DAE.BPR_0 <> ' ' THEN DAE.BPR_0
				WHEN NOT (DAE.BPR_0 is null OR SUBSTRING(DAE.ACC_0, 1, 3) = '445' OR SUBSTRING(DAE.ACC_0, 1, 1) = '5') THEN DAE.OFFACC_0
				ELSE ' '
			END)
			 as Contrepartie,
			DAE.BPR_0 as Tiers,
			DAE.AMTLED_0 * DAE.SNS_0 as Montant,
			DAE.CSLFLO_0 as Flux,
			GAC.DES_0 as Lib_Cpte,
			BPR.BPRNAM_0 as Lib_Tiers
			FROM x3v12prod.PROSOL2.GACCENTRY HAE
			INNER JOIN x3v12prod.PROSOL2.GACCENTRYD DAE ON HAE.TYP_0 = DAE.TYP_0 AND HAE.NUM_0 = DAE.NUM_0 AND DAE.LEDTYP_0 = @1
			INNER JOIN x3v12prod.PROSOL2.GACCOUNT GAC ON GAC.ACC_0 = DAE.ACC_0 AND GAC.COA_0 = DAE.COA_0
			LEFT OUTER JOIN x3v12prod.PROSOL2.FACGROUP FGR ON FGR.FCY_0 = HAE.FCY_0
			LEFT OUTER JOIN x3v12prod.PROSOL2.BPARTNER BPR ON BPR.BPRNUM_0 = DAE.BPR_0 and BPR.GRUCOD_0 <> ' '
		WHERE
			(FGR.CPY_0 = @2 OR (@2 = ' ' AND FGR.CPY_0 = DAE.CPY_0))
			AND (DAE.CPY_0 >= @3 OR @3 = ' ') AND (DAE.CPY_0 <= @4 OR @4 = ' ')
			AND DAE.ACCDAT_0 >= @5 AND DAE.ACCDAT_0 <= @6
			AND (DAE.ACC_0 >= @7 OR @7 = ' ') AND (DAE.ACC_0 <= @8 OR @8 = ' ')
			AND (CAST(@9 as INT) = 0 OR NOT (HAE.JOU_0 = 'RAN' AND DAE.ACCDAT_0 <> @5))
	) Fact_Data
	LEFT OUTER JOIN x3v12prod.PROSOL2.ZSOCCONSO SC1 on SC1.SOCIETE_0 = Fact_Data.Societe
	LEFT OUTER JOIN x3v12prod.PROSOL2.BPARTNER BPR ON BPR.BPRNUM_0 = Fact_Data.Contrepartie
	LEFT OUTER JOIN x3v12prod.PROSOL2.ZSOCCONSO SC2 on SC2.SITE_0 = BPR.GRUCOD_0
GROUP BY SC1.CONSO_0, Site, Compte, coalesce(SC2.CONSO_0, BPR.GRUCOD_0), Tiers, Lib_Cpte, Lib_Tiers
HAVING SUM(Montant) <> 0
ORDER BY SC1.CONSO_0, Site, Compte, Tiers

