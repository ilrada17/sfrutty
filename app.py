def analizza_pdf_perseo(pdf_file):
    ore_standard_base = {0: 8.0, 1: 8.0, 2: 8.0, 3: 8.0, 4: 4.0, 5: 0.0, 6: 0.0}
    nomi_giorni = ["Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"]
    
    turni_estratti = []
    
    # Queste variabili "ricordano" l'ultimo giorno trovato
    giorno_in_memoria = None 
    idx_in_memoria = None

    with pdfplumber.open(pdf_file) as pdf:
        for pagina in pdf.pages:
            testo = pagina.extract_text()
            if not testo: continue
            linee = testo.split('\n')
            
            for linea in linee:
                # 1. CERCA UNA NUOVA DATA
                match_data = re.search(r'(\d{2}\s+(?:Lun|Mar|Mer|Gio|Gia|Ven|Sab|Sah|Dom)|(\d{2}/\d{2}/\d{4}))', linea, re.IGNORECASE)
                
                if match_data:
                    giorno_in_memoria = match_data.group(1).strip()
                    # Identifica il giorno della settimana per lo standard (8h o 4h)
                    low_g = giorno_in_memoria.lower()
                    if 'lun' in low_g: idx_in_memoria = 0
                    elif 'mar' in low_g: idx_in_memoria = 1
                    elif 'mer' in low_g: idx_in_memoria = 2
                    elif 'gio' in low_g or 'gia' in low_g: idx_in_memoria = 3
                    elif 'ven' in low_g: idx_in_memoria = 4
                    elif 'sab' in low_g or 'sah' in low_g: idx_in_memoria = 5
                    elif 'dom' in low_g: idx_in_memoria = 6
                
                # 2. SE ABBIAMO UNA DATA IN MEMORIA, CERCHIAMO GLI ORARI
                if giorno_in_memoria:
                    orari = re.findall(r'\b\d{2}:\d{2}\b', linea)
                    
                    for i in range(0, len(orari) // 2 * 2, 2):
                        ora_in, ora_fi = orari[i], orari[i+1]
                        if ora_in == "00:00" and ora_fi == "00:00": continue

                        t_in = converti_in_timedelta(ora_in)
                        t_fi = converti_in_timedelta(ora_fi)
                        t_fi_eff = t_fi + timedelta(hours=24) if t_fi <= t_in else t_fi
                        durata = (t_fi_eff - t_in).total_seconds() / 3600
                        
                        is_cont = (t_in <= timedelta(hours=14)) and (t_fi_eff >= timedelta(hours=15, minutes=30))

                        turni_estratti.append({
                            "Data": giorno_in_memoria,
                            "Giorno_Idx": idx_in_memoria,
                            "Giorno": nomi_giorni[idx_in_memoria] if idx_in_memoria is not None else "Sconosciuto",
                            "Inizio": ora_in,
                            "Fine": ora_fi,
                            "Ore_Fatte": durata,
                            "Is_Cont": is_cont
                        })
    # ... (il resto del codice per raggruppare e calcolare lo straordinario rimane uguale)
