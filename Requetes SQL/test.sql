/* Infos requête
Code : ZZZZIMMOBFC
Description : Requete Test
*/

DECLARE
@1 as date = '2021-13-01',	-- Date Début|0|
@2 as date = '2021-12-31',	-- Date Fin|0|month_end(-1)
@3 as nchar(5) = 'TEST',	-- Société (facultatif)|1
@4 as nchar(0) = ' ',	-- Cpte immos début (facultatif)|1
@5 as nchar(7) = '20',	-- Cpte immos fin (facultatif)|1
@6 as nvarchar(10) = 'FRAAAAAAAAAAA'	-- Plan de compte|0
;

SELECT
	convert(varchar, year(@2)) + '.' + right('0'+convert(varchar,month(@2)),2),
	SC1.CONSO_0 as Societe,
	Cpte, Flux, ROUND(SUM(Montant), 0) as Montant
FROM(
	SELECT
		Societe,
		(CASE	WHEN Rubrique = 'Amort_Rep' THEN Cpte_Amort
				ELSE Cpte_Brut END) as Cpte,
		(CASE	WHEN Rubrique = 'Immo_Entrees' THEN 'F20'
				WHEN Rubrique = 'Immo_Trsf_Cpte' THEN 'F50'
				ELSE 'F30' END) as Flux,
		Montant
	FROM
	(
		SELECT
			/* Infos Immo */
			F.CPY_0 as Societe,
			C.ACC_0 as Cpte_Brut, C.ACC_1 as Cpte_Amort,
			
			/* Infos entrées */
			CASE WHEN C.ACC_0 LIKE '275%' OR C.ACC_0 LIKE '278%' THEN CAST(FLU.INCBUY as FLOAT(2)) ELSE 0 END as Immo_Entrees,
			
			/* Infos sorties */
			CAST(-FLU.DCRSAL as FLOAT(2)) as Immo_Sorties,
			CAST(-(FLU.INCTRF - FLU.DCRTRF) as FLOAT(2)) as Immo_Trsf_Cpte,
			CAST((CASE WHEN F.GAC_0 = FLU.GAC AND F.ISSDAT_0 <= @2 AND F.ISSDAT_0 >= D.FIYSTRDAT_0 AND F.ISSDAT_0 >= @1 THEN D.DPRCUM_0 + D.PERCLOCUM_0 + D.PERENDDPE_0 ELSE 0 END) as FLOAT(2)) as Amort_Rep
		FROM
			x3v12prod.PROSOL2.FXDASSETS F 
			INNER JOIN x3v12prod.PROSOL2.DEPREC D ON D.DPRPLN_0 = 1 AND F.AASREF_0 = D.AASREF_0 AND D.PERENDDAT_0 = @2
			INNER JOIN x3v12prod.PROSOL2.GACCCODE C ON F.ACCCOD_0 = C.ACCCOD_0 AND C.COA_0 = @6
			INNER JOIN (
					SELECT 	FLU_TMP.CPY_0 as CPY, FLU_TMP.FCY_0 as FCY, FLU_TMP.GAC_0 as GAC, FLU_TMP.AASREF_0 as AASREF,
							SUM(FLU_TMP.INCBUY_0) as INCBUY,
							SUM(FLU_TMP.INCTRF_0) as INCTRF,
							SUM(CASE WHEN FLU_TMP.PERENDDAT_0 = Info_Periodes.Periode_Max THEN FLU_TMP.DCRSAL_0 ELSE 0 END) as DCRSAL,
							SUM(FLU_TMP.DCRTRF_0) as DCRTRF
					FROM x3v12prod.PROSOL2.FXDLIFL FLU_TMP
						 LEFT OUTER JOIN (	SELECT AASREF_0, MAX(PERENDDAT_0) as Periode_Max
											FROM x3v12prod.PROSOL2.FXDLIFL WHERE TYP_0 = 2 AND COA_0 = @6 AND PERSTRDAT_0 >= @1 AND PERENDDAT_0 <= @2 GROUP BY AASREF_0) as Info_Periodes
						 ON Info_Periodes.AASREF_0 = FLU_TMP.AASREF_0
					WHERE FLU_TMP.DPRPLN_0 = 1 AND FLU_TMP.TYP_0 = 2 AND FLU_TMP.COA_0 = @6 AND FLU_TMP.PERSTRDAT_0 >= @1 AND FLU_TMP.PERENDDAT_0 <= @2
					GROUP BY FLU_TMP.CPY_0, FLU_TMP.FCY_0, FLU_TMP.GAC_0, FLU_TMP.AASREF_0
				) as FLU ON FLU.AASREF = F.AASREF_0
		WHERE
			(F.CPY_0 = @3 OR @3 = ' ') /* filtre des sites */
			AND (C.ACC_0 >= @4 OR @4 = ' ')  /* filtre comptes début */
			AND (C.ACC_0 <= @5 OR @5 = ' ')  /* filtre comptes fin */
	) as Fact_Data
	UNPIVOT(
		Montant
		FOR Rubrique in (Immo_Entrees, Immo_Sorties, Immo_Trsf_Cpte, Amort_Rep)
	) as Data_Unpivot
	WHERE Montant <> 0
) as Fact_Data_Unpivoted
LEFT OUTER JOIN PROSOL2.ZSOCCONSO SC1 ON SC1.SOCIETE_0 = Fact_Data_Unpivoted.Societe
GROUP BY SC1.CONSO_0, Cpte, Flux
HAVING SUM(Montant) <> 0
ORDER BY SC1.CONSO_0, Cpte, Flux
