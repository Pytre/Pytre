/* Infos requête
Code : YRAPROC3
Description : Etat des Immobilisations
*/

DECLARE
@1 as date = '2021-10-01',	-- Date Début|0|fiscal_year(9, -12, 1)
@2 as date = '2021-12-31 ',	-- Date Fin|0|month_end(-1)
@3 as nvarchar(5) = ' ',	-- Regroup.Sté (facultatif)|1
@4 as nchar(5) = ' ',	-- Société début (facultatif)|1
@5 as nchar(5) = ' ',	-- Société fin (facultatif)|1
@6 as nchar(7) = ' ',	-- Cpte immos début (facultatif)|1
@7 as nchar(7) = ' ',	-- Cpte immos fin (facultatif)|1
@8 as int = 1,	-- Acquisitions seulement (2 pour oui, 1 pour non)|0
@9 as int = 1,	-- Sorties seulement (2 pour oui, 1 pour non)|0
@10 as int = 1,	-- Dérogatoire seulement (2 pour oui, 1 pour non)|0
@11 as nvarchar(10) = 'FRA',	-- Plan de compte (défaut : FRA)|0
@12 as int = 2	-- Plan amort fiscal (défaut : 2)|0
;

SELECT
	/* Infos Immo */
	F.CPY_0 as Societe, F.FCY_0 as Site,
	FLU.GAC as Cpte_Cptable,
	F.CCE_3 as Axe_4,
	F.AASREF_0 as Bien, F.ACGGRP_0 as Famille, F.AASDES1_0 as Designation, F.AASDES2_0 as Designation_2,
	C.ACC_0 as Cpte_Brut, C.ACC_1 as Cpte_Amort,
	F.PURDAT_0 as Date_Achat, F.ITSDAT_0 as Mise_En_Service,
	
	D.DPM_0 as Mode_Amort, D.DPRDUR_0 as Duree_Amort,
	
	/* Variation Immos */
	FLU.BSEVALSTR as Immo_Debut,
	(CASE WHEN FLU.Cpte_Cptable_Fin <> FLU.GAC OR (F.ISSDAT_0 <= @2 AND F.ISSDAT_0 >= '1901-01-01') THEN 0 ELSE FLU.BSEVALEND END) - (FLU.INCBUY - FLU.DCRSAL) - (FLU.INCTRF - FLU.DCRTRF) - (FLU.INCFCY - FLU.DCRFCY) - FLU.BSEVALSTR as Immo_Correct_Var,
	FLU.INCBUY as Immo_Entrees,
	FLU.DCRSAL as Immo_Sorties,
	FLU.INCTRF - FLU.DCRTRF as Immo_Trsf_Cpte,
	FLU.INCFCY - FLU.DCRFCY as Immo_Trsf_Site,
	(CASE WHEN FLU.Cpte_Cptable_Fin <> FLU.GAC OR (F.ISSDAT_0 <= @2 AND F.ISSDAT_0 >= '1901-01-01') THEN 0 ELSE FLU.BSEVALEND END) as Immo_Fin,
	
	/* Variation Amort */
	FLU.DPRCUMSTR as Amort_Debut,
	(CASE WHEN FLU.Cpte_Cptable_Fin <> FLU.GAC OR (F.ISSDAT_0 <= @2 AND F.ISSDAT_0 >= '1901-01-01') THEN 0 ELSE FLU.DPRCUMEND END) - (FLU.DPE - FLU.DCRDPEOUT) - (FLU.INCDPRTRF - FLU.DCRDPRTRF) - (FLU.INCDPRFCY - FLU.DCRDPRFCY) - FLU.DPRCUMSTR as Amort_Correct_Var,
	FLU.DPE as Amort_Dot,
	FLU.DCRDPEOUT as Amort_Rep,
	FLU.INCDPRTRF - FLU.DCRDPRTRF as Amort_Trsf_Cpte,
	FLU.INCDPRFCY - FLU.DCRDPRFCY as Amort_Trsf_Site,
	(CASE WHEN FLU.Cpte_Cptable_Fin <> FLU.GAC OR (F.ISSDAT_0 <= @2 AND F.ISSDAT_0 >= '1901-01-01') THEN 0 ELSE FLU.DPRCUMEND END) as Amort_Fin,
	
	/* VNC Immos */
	(CASE WHEN FLU.Cpte_Cptable_Fin <> FLU.GAC OR (F.ISSDAT_0 <= @2 AND F.ISSDAT_0 >= '1901-01-01') THEN 0 ELSE F.ACGETRNOT_0 - D.DPRCUM_0 - D.PERCLOCUM_0 - D.PERENDDPE_0 END) as Valeur_Nette,
	
	/* Pdts et charges Immos Sorties */
	(CASE WHEN FLU.Cpte_Cptable_Fin = FLU.GAC AND F.ISSDAT_0 <= @2 /* AND F.ISSDAT_0 >= @1 */ THEN F.ISSDAT_0 ELSE NULL END) as Date_Sortie,
	(CASE WHEN FLU.Cpte_Cptable_Fin = FLU.GAC AND F.ISSDAT_0 <= @2 AND F.ISSTYP_0 > 0 /* AND F.ISSDAT_0 >= @1 */ THEN CAST(F.ISSTYP_0 as VARCHAR) + ' - ' + COALESCE(AST.LANMES_0, '') ELSE '' END) as Motif_Sortie,
	(CASE WHEN FLU.Cpte_Cptable_Fin = FLU.GAC AND F.ISSDAT_0 <= @2 AND F.ISSDAT_0 >= @1 AND CAST(C.ACC_0 as VARCHAR) NOT LIKE '275%' THEN F.ISSAMT_0 ELSE 0 END) as Pdts_Cessions,
	(CASE WHEN FLU.Cpte_Cptable_Fin = FLU.GAC AND F.ISSDAT_0 <= @2 AND F.ISSDAT_0 >= @1 AND CAST(C.ACC_0 as VARCHAR) NOT LIKE '275%' THEN F.ACGETRNOT_0 - D.DPRCUM_0 - D.PERCLOCUM_0 - D.PERENDDPE_0 ELSE 0 END) as VNC_Immo_Sorties,
	
	/* Infos et variation du dérogatoire */
	(CASE WHEN D.DPM_0 <> D2.DPM_0 OR D.DPRDUR_0 <> D2.DPRDUR_0 THEN D2.DPM_0 ELSE 'mode et durée iso cpta' END) as Mode_Amort_Fisc, 
	D2.DPRDUR_0 as Duree_Amort_Fisc,
	(CASE WHEN FLU.Cpte_Cptable_Fin = FLU.GAC THEN D.LEGCUM_0 - D.LEGRVECUM_0 ELSE 0 END) as Cumul_Derog_Debut,
	(CASE WHEN FLU.Cpte_Cptable_Fin = FLU.GAC THEN D.PERLEGCUM_0 + D.LEG_0 ELSE 0 END) as Dot_Derog,
	(CASE WHEN FLU.Cpte_Cptable_Fin = FLU.GAC THEN D.PERLEGRVE_0 + D.LEGRVE_0 + (CASE WHEN F.ISSDAT_0 <= @2 AND F.ISSDAT_0 >= @1 THEN F.DERRVEISS_0 ELSE 0 END) ELSE 0 END) as Rep_Derog,
	(CASE WHEN FLU.Cpte_Cptable_Fin <> FLU.GAC OR (F.ISSDAT_0 <= @2 AND F.ISSDAT_0 >= '1901-01-01') THEN 0 ELSE D.LEGCUM_0 - D.LEGRVECUM_0 + D.PERLEGCUM_0 + D.LEG_0 - D.PERLEGRVE_0 - D.LEGRVE_0 END) as Cumul_Derog_Fin,
	
	/* Autres infos */
	@1 as Date_Debut,
	D.PERENDDAT_0 as Date_Fin
FROM
	x3v12prod.PROSOL2.FXDASSETS F 
	INNER JOIN x3v12prod.PROSOL2.DEPREC D ON D.DPRPLN_0 = 1 AND F.AASREF_0 = D.AASREF_0 AND D.PERENDDAT_0 = @2
	INNER JOIN x3v12prod.PROSOL2.DEPREC D2 ON D2.DPRPLN_0 = @12 AND F.AASREF_0 = D2.AASREF_0 AND D2.PERENDDAT_0 = @2 /* Plan amort fiscal : pour France = 2 ; pour Italie = 7 */
	INNER JOIN x3v12prod.PROSOL2.GACCCODE C ON F.ACCCOD_0 = C.ACCCOD_0 AND C.COA_0 = @11
	INNER JOIN (
			SELECT 	FLU_TMP.CPY_0 as CPY, FLU_TMP.FCY_0 as FCY, FLU_TMP.GAC_0 as GAC, FLU_TMP.AASREF_0 as AASREF,
					MAX(CASE WHEN FLU_TMP.PERENDDAT_0 = Info_Periodes.Periode_Max THEN FLU_TMP.GAC_0 ELSE 0 END) as Cpte_Cptable_Fin,
					
					SUM(CASE WHEN FLU_TMP.PERSTRDAT_0 = @1 THEN COALESCE(Info_AN.BSEVALEND_0, 0) ELSE 0 END) as BSEVALSTR, /* valeur début immo */
					SUM(FLU_TMP.INCBUY_0) as INCBUY, SUM(FLU_TMP.INCFCY_0) as INCFCY, SUM(FLU_TMP.INCTRF_0) as INCTRF, /* flux augmentation du brut */
					SUM(CASE WHEN FLU_TMP.PERENDDAT_0 = Info_Periodes.Periode_Max THEN FLU_TMP.DCRSAL_0 ELSE 0 END) as DCRSAL, /* flux diminution du brut, hors trsf */
					SUM(FLU_TMP.DCRFCY_0) as DCRFCY, SUM(FLU_TMP.DCRTRF_0) as DCRTRF, /* flux diminution du brut, trsf uniquement */
					SUM(CASE WHEN FLU_TMP.PERENDDAT_0 = @2 THEN FLU_TMP.BSEVALEND_0 ELSE 0 END) as BSEVALEND, /* valeur fin immo */
					
					SUM(CASE WHEN FLU_TMP.PERSTRDAT_0 = @1 THEN COALESCE(Info_AN.DPRCUMEND_0, 0) ELSE 0 END) as DPRCUMSTR, /* valeur début amort */
					SUM(FLU_TMP.DPE_0) as DPE, SUM(FLU_TMP.INCDPRFCY_0) as INCDPRFCY, SUM(FLU_TMP.INCDPRTRF_0) as INCDPRTRF, /* flux augmentation amort */
					SUM(CASE WHEN FLU_TMP.PERENDDAT_0 = Info_Periodes.Periode_Max THEN FLU_TMP.DCRDPEOUT_0 ELSE 0 END) as DCRDPEOUT, /* flux diminution amort, hors trsf */
					SUM(FLU_TMP.DCRDPRFCY_0) as DCRDPRFCY, SUM(FLU_TMP.DCRDPRTRF_0) as DCRDPRTRF, /* flux diminution amort, trsf uniquement */
					SUM(CASE WHEN FLU_TMP.PERENDDAT_0 = @2 THEN FLU_TMP.DPRCUMEND_0 ELSE 0 END) as DPRCUMEND, /* valeur fin amort */
					
					SUM(FLU_TMP.ISSAMT_0) as ISSAMT /* montant sortie */
			FROM x3v12prod.PROSOL2.FXDLIFL FLU_TMP
				 LEFT OUTER JOIN (	SELECT AASREF_0, MAX(PERENDDAT_0) as Periode_Max
				 					FROM x3v12prod.PROSOL2.FXDLIFL WHERE TYP_0 = 2 AND COA_0 = @11 AND PERSTRDAT_0 >= @1 AND PERENDDAT_0 <= @2 GROUP BY AASREF_0) as Info_Periodes
				 ON Info_Periodes.AASREF_0 = FLU_TMP.AASREF_0
				 LEFT OUTER JOIN (	SELECT AASREF_0, GAC_0, BSEVALEND_0, DPRCUMEND_0
									FROM x3v12prod.PROSOL2.FXDLIFL WHERE DPRPLN_0 = 1 AND TYP_0 = 2 AND COA_0 = @11 /* AND PERSTRDAT_0 >= '1901-01-01' */ AND PERENDDAT_0 = DATEADD(day, -1, @1)) as Info_AN
				 ON Info_AN.AASREF_0 = FLU_TMP.AASREF_0 AND Info_AN.GAC_0 = FLU_TMP.GAC_0 
			WHERE FLU_TMP.DPRPLN_0 = 1 AND FLU_TMP.TYP_0 = 2 AND FLU_TMP.COA_0 = @11 AND FLU_TMP.PERSTRDAT_0 >= @1 AND FLU_TMP.PERENDDAT_0 <= @2
			GROUP BY FLU_TMP.CPY_0, FLU_TMP.FCY_0, FLU_TMP.GAC_0, FLU_TMP.AASREF_0
		) as FLU ON FLU.AASREF = F.AASREF_0
	LEFT OUTER JOIN x3v12prod.PROSOL2.APLSTD AST ON AST.LANNUM_0 = F.ISSTYP_0 AND AST.LANCHP_0 = 3159 AND AST.LAN_0 = 'FRA' /* menu des motifs de sortie */
	LEFT OUTER JOIN x3v12prod.PROSOL2.FACGROUP FGR ON FGR.FCY_0 = F.FCY_0
WHERE
	(FGR.CPY_0 = @3 OR (@3 = ' ' AND FGR.CPY_0 = F.CPY_0)) /* filtre des groupes de stés */
	AND (F.CPY_0 >= @4 OR @4 = ' ') AND (F.CPY_0 <= @5 OR @5 = ' ') /* filtre des stés */
	AND (C.ACC_0 >= @6 OR @6 = ' ')  /* filtre comptes début */
	AND (C.ACC_0 <= @7 OR @7 = ' ')  /* filtre comptes fin */
	AND (
			(F.PURDAT_0 >= @1 AND F.PURDAT_0 <= @2 AND @8 = 2) /* quand filtre des entrées (param 8 est sur oui) garder les entréess */
		OR	(F.ISSDAT_0 <= @2 AND F.ISSDAT_0 >= '1901-01-01' AND F.ISSDAT_0 >= @1 AND @9 = 2) /* quand filtre des sorties (param 9 est sur oui) garder les sorties */
		OR	(@8 <> 2 AND @9 <> 2) /* si ni filtre entrée, ni filtre sortie alors tout prendre */
	)
	AND (@10 <> 2 OR (D.DPM_0 <> D2.DPM_0 OR D.DPRDUR_0 <> D2.DPRDUR_0)) /* filtrage dérogatoire sauf si param 10 pas sur oui */
ORDER BY 
	F.CPY_0, FLU.GAC, F.AASREF_0
