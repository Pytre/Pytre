/* Infos requête
Code : ZBALDECL
Description : Balance déclinée
*/

DECLARE
@1 as int = 4,	-- Référentiel (1 pour social, 4 pour situ)
@2 as nvarchar(5) = 'GF521',	-- Regroup.Sté (optionnel)|1
@3 as nchar(5) = ' ',	-- Société Début (optionnel)|1
@4 as nchar(5) = ' ',	-- Société Fin (optionnel)|1
@5 as date = '2020-10-01',	-- Date Début|0|fiscal_year(9, -12, 1)
@6 as date = '2021-09-30',	-- Date Fin|0|month_end(-1)
@7 as nchar(7) = ' ',	-- Compte Début (optionnel)|1
@8 as nchar(7) = ' ',	-- Compte Fin (optionnel)|1
@9 as int = 1,	-- Détail Tiers (1 pour oui, 0 pour non)
@10 as int = 1,	-- Détail Contrepartie (1 pour oui, 0 pour non)
@11 as int = 1,	-- Libellé Compte (1 pour oui, 0 pour non)
@12 as int = 1,	-- Sans RAN > Date Début (1 pour oui, 0 pour non)
@13 as int = 0,	-- Détail Ana Serv, BU, Ens (1 pour oui, 0 pour non)
@14 as int = 1,	-- Détail Ana Nature (1 pour oui, 0 pour non)
@15 as int = 0,	-- Détail Ana Projet (1 pour oui, 0 pour non)
@16 as date = ' ',	-- Date Début N-1 (optionnelle)|1
@17 as date = ' '/*,*/	-- Date Fin N-1 (optionnelle)|1
-- @18 as int = 1 -- Rupture mois (1 pour oui, 0 pour non)
;

SELECT
	Societe, Site, Compte, GRUCOD_0 as IdDecli, Tiers, Contrepartie, SUM(Montant) as Montant, Lib_Cpte, Lib_Tiers,
	Section_Ana_Service, Section_Ana_BU, Section_Ana_Enseigne, Section_Ana_Nature, Section_Ana_Projet,
	SUM(Montant_Comparaison) as Montant_Comparaison,
	CASE WHEN @16 > '2001-01-01' AND @17 > '2001-01-01' THEN SUM(Montant - Montant_Comparaison) ELSE 0 END as Montant_Vs,
	CASE
		WHEN GRUCOD_0 IS NULL OR GRUCOD_0 = '' THEN ''
		WHEN Societe < coalesce(SOC.CONSO_0, GRUCOD_0) THEN Societe + '-' + coalesce(SOC.CONSO_0, GRUCOD_0)
		ELSE coalesce(SOC.CONSO_0, GRUCOD_0) + '-' + Societe
	END as Couple_Interco /*,
	Rupture_Mois*/
FROM
	(	SELECT
			DAE.CPY_0 as Societe,
			DAE.FCYLIN_0 as Site,
			DAE.ACC_0 as Compte,
			(CASE WHEN CAST(@9 as INT) = 0 THEN '' ELSE DAE.BPR_0 END) as Tiers,
			(CASE
				WHEN CAST(@10 as INT) = 0 OR DAE.BPR_0 <> ' ' OR SUBSTRING(DAE.ACC_0, 1, 1) NOT IN ('6', '7') THEN '' ELSE DAE.OFFACC_0
			END) as Contrepartie,
			(CASE
				WHEN NOT (CAST(@10 as INT) = 0 OR DAE.BPR_0 <> ' ' OR SUBSTRING(DAE.ACC_0, 1, 1) NOT IN ('6', '7')) THEN DAE.OFFACC_0
				WHEN CAST(@9 as INT) <> 0 THEN DAE.BPR_0
				ELSE ' '
			END) as Cle_Avec_BPARTNER,
			CASE WHEN DAE.ACCDAT_0 >= @5 AND DAE.ACCDAT_0 <= @6
				THEN (CASE WHEN DAA.NUM_0 IS NULL THEN DAE.AMTLED_0 * DAE.SNS_0 ELSE DAA.AMTLED_0 * DAA.SNS_0 END)
				ELSE 0
			END as Montant,
			CASE WHEN DAE.ACCDAT_0 >= @16 AND DAE.ACCDAT_0 <= @17 AND @16 > '2001-01-01' AND @17 > '2001-01-01'
				THEN (CASE WHEN DAA.NUM_0 IS NULL THEN DAE.AMTLED_0 * DAE.SNS_0 ELSE DAA.AMTLED_0 * DAA.SNS_0 END)
				ELSE 0
			END as Montant_Comparaison,
			(CASE WHEN CAST(@11 as INT) = 0 THEN '' ELSE GAC.DES_0 END) as Lib_Cpte,
			(CASE WHEN CAST(@11 as INT) = 0 OR @9 = 0 THEN '' ELSE BPR.BPRNAM_0 END) as Lib_Tiers,
			(CASE WHEN CAST(@13 as INT) = 1 THEN DAA.CCE_0 ELSE '' END) as Section_Ana_Service,
			(CASE WHEN CAST(@13 as INT) = 1 THEN DAA.CCE_1 ELSE '' END) as Section_Ana_BU,
			(CASE WHEN CAST(@13 as INT) = 1 THEN DAA.CCE_2 ELSE '' END) as Section_Ana_Enseigne,
			(CASE WHEN CAST(@14 as INT) = 1 THEN DAA.CCE_3 ELSE '' END) as Section_Ana_Nature,
			(CASE WHEN CAST(@15 as INT) = 1 THEN DAA.CCE_4 ELSE '' END) as Section_Ana_Projet/*,
			(CASE WHEN CAST(@18 as INT) = 1 THEN CAST ((CASE WHEN DAE.ACCDAT_0 >= @5 AND DAE.ACCDAT_0 <= @6 THEN 0 ELSE 1 END) + YEAR(DAE.ACCDAT_0) as VARCHAR(4)) + '-' + FORMAT(MONTH(DAE.ACCDAT_0), '00')	ELSE NULL END) as Rupture_Mois*/
		FROM
			x3v12prod.PROSOL2.GACCENTRY HAE
			INNER JOIN x3v12prod.PROSOL2.GACCENTRYD DAE ON HAE.TYP_0 = DAE.TYP_0 AND HAE.NUM_0 = DAE.NUM_0 AND DAE.LEDTYP_0 = @1
			LEFT OUTER JOIN x3v12prod.PROSOL2.GACCENTRYA DAA ON HAE.TYP_0 = DAA.TYP_0 AND HAE.NUM_0 = DAA.NUM_0 AND DAE.LIN_0 = DAA.LIN_0
			LEFT OUTER JOIN x3v12prod.PROSOL2.FACGROUP FGR ON FGR.FCY_0 = HAE.FCY_0
			INNER JOIN x3v12prod.PROSOL2.GACCOUNT GAC ON GAC.ACC_0 = DAE.ACC_0 AND GAC.COA_0 = DAE.COA_0
			LEFT OUTER JOIN x3v12prod.PROSOL2.BPARTNER BPR ON BPR.BPRNUM_0 = DAE.BPR_0
		WHERE
			(FGR.CPY_0 = @2 OR (@2 = ' ' AND FGR.CPY_0 = DAE.CPY_0))
			AND (DAE.CPY_0 >= @3 OR @3 = ' ') AND (DAE.CPY_0 <= @4 OR @4 = ' ')
			AND (DAE.ACC_0 >= @7 OR @7 = ' ') AND (DAE.ACC_0 <= @8 OR @8 = ' ') AND DAE.ACC_0 <> ' '
			AND ((
					/* Dates pour info N */
					DAE.ACCDAT_0 >= @5 AND DAE.ACCDAT_0 <= @6
					AND (CAST(@12 as INT) = 0 OR NOT (HAE.FLGREP_0 = 2 AND DAE.ACCDAT_0 <> @5)) /* ignorer report à nouveau <> de date début demandée ; ancienne méthode : HAE.JOU_0 = 'RAN' */
				) OR (
					/* Dates pour info N-1 */
					DAE.ACCDAT_0 >= @16 AND DAE.ACCDAT_0 <= @17 AND @16 > '2001-01-01' AND @17 > '2001-01-01' /* pour ne rien renvoyer si variable date début et fin N-1 non renseignées */
					AND (CAST(@12 as INT) = 0 OR NOT (HAE.FLGREP_0 = 2 AND DAE.ACCDAT_0 <> @16)) /* ignorer report à nouveau <> de date début demandée ; ancienne méthode : HAE.JOU_0 = 'RAN' */				
				))
			AND HAE.JOU_0 <> 'CHIUS' /* ignorer journal de clôture généré sur l'Italie */
	) Fact_Data
	LEFT OUTER JOIN x3v12prod.PROSOL2.BPARTNER BPR ON BPR.BPRNUM_0 = Fact_Data.Cle_Avec_BPARTNER
	LEFT OUTER JOIN x3v12prod.PROSOL2.ZSOCCONSO SOC on SOC.SITE_0 = BPR.GRUCOD_0
GROUP BY Societe, Site, Compte, GRUCOD_0, Tiers, Contrepartie, Lib_Cpte, Lib_Tiers, Section_Ana_Service, Section_Ana_BU, Section_Ana_Enseigne, Section_Ana_Nature, Section_Ana_Projet, SOC.CONSO_0/*, Rupture_Mois*/
HAVING SUM(Montant) <> 0 OR SUM(Montant_Comparaison) <> 0
ORDER BY Societe, Site, Compte, GRUCOD_0, Tiers, Contrepartie, Section_Ana_Service, Section_Ana_BU, Section_Ana_Enseigne, Section_Ana_Nature, Section_Ana_Projet/*, Rupture_Mois*/
