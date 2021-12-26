	/* Contrôle code conso sur fiche sté */
	SELECT
		'Fiche Sté' as Categorie, CPY_0 as Code, GRUCOD_0 as Code_Conso, '' as Ste_Juridique
	FROM
		x3v12prod.PROSOL2.COMPANY CPY
	WHERE 
		CPYLEGFLG_0 = 2 -- sté juridique (et pas groupe de sté)
		AND CPY_0 <> GRUCOD_0 
UNION ALL
	/* Contrôle code conso sur fiche tiers */
	SELECT
		'Fiche Tiers' as Categorie, BPR.BPRNUM_0 as Code, BPR.GRUCOD_0 as Code_Conso, FCY.LEGCPY_0 as Ste_Juridique
	FROM
		x3v12prod.PROSOL2.BPARTNER BPR LEFT OUTER JOIN x3v12prod.PROSOL2.FACILITY FCY ON FCY.FCY_0 = BPR.GRUCOD_0
	WHERE
		BPRNUM_0 LIKE '_____'
		AND BPR.BPRNUM_0 <> FCY.LEGCPY_0 AND BPR.BPRNUM_0 <> BPR.GRUCOD_0

