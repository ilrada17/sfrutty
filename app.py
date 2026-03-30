import streamlit as st
import pdfplumber
import re
import pandas as pd
import io
from datetime import datetime, timedelta

st.set_page_config(page_title="Calcolatore Perseo3", layout="wide")
st.title("📊 Estrattore Ore Perseo3 Aeronautica")

def converti_in_timedelta(ora_str):
    if not ora_str or ":" not in ora_str: return timedelta(0)
    try:
        ore, minuti = map(int, ora_str.split(':'))
        return timedelta(hours=ore, minutes=minuti)
    except: return timedelta(0)

def formatta_hhmm(ore_decimali):
    ore = int(ore_decimali)
    minuti = int(round((ore_decimali - ore) * 60))
    return f"{ore:02d}:{minuti:02d}"

def analizza_pdf_perseo(pdf_file):
    # Standard: Lun-Gio 8h, Ven 4h
    ore_standard_base = {0: 8.0, 1: 8.0, 2: 8.0, 3: 8.0, 4: 4.0, 5: 0.0, 6: 0.0}
    nomi_giorni = ["Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"]
    
    dati_righe = []
    giorno_corrente = None
    giorno_sett_idx = None

    with pdfplumber.open(pdf_file) as pdf:
        for pagina in pdf.pages:
            testo = pagina.extract_text()
            if not testo: continue
            
            # Pulizia: rimuoviamo i ritorni a capo eccessivi che spezzano le righe
            linee = testo.replace('\n\n', '\n').split('\n')
            
            for linea in linee:
                # 1. IDENTIFICAZIONE DATA (es. 01 Mer o 01/01/2026)
                match_data = re.search(r'(\d{2}\s+(?:Lun|Mar|Mer|Gio|Gia|Ven|Sab|Sah|Dom)|(\d{2}/\d{2}/\d{4}))', linea, re.IGNORECASE)
                
                if match_data:
                    giorno_corrente = match_data.group(1).strip()
                    # Mapping manuale per Perseo (gestisce anche "Gia" o "Sah")
                    low_g = giorno_corrente.lower()
                    if 'lun' in low_g: giorno_sett_idx = 0
                    elif 'mar' in low_g: giorno_sett_idx = 1
                    elif 'mer' in low_g: giorno_sett_idx = 2
                    elif 'gio' in low_g or 'gia' in low_g: giorno_sett_idx = 3
                    elif 'ven' in low_g: giorno_sett_idx = 4
                    elif 'sab' in low_g or 'sah' in low_g: giorno_sett_idx = 5
                    elif 'dom' in low_g: giorno_sett_idx = 6
                    else:
                        try: # Prova formato data estesa
                            giorno_sett_idx = datetime.strptime(re.search(r'\d{2}/\d{2}/\d{4}', giorno_corrente).group(), "%d/%m/%Y").weekday()
                        except: pass

                if giorno_sett_idx is None: continue

                # 2. ESTRAZIONE ORARI (cerca tutti i pattern HH:MM)
                orari = re.findall(r'\b\d{2}:\d{2}\b', linea)
                
                # Se la riga ha orari (es. 08:00 16:30)
                for i in range(0, len(orari) // 2 * 2, 2):
                    ora_in, ora_fi = orari[i], orari[i+1]
                    if ora_in == "00:00" and ora_fi == "00:00": continue

                    t_in = converti_in_timedelta(ora_in)
                    t_fi = converti_in_timedelta(ora_fi)
                    t_fi_eff = t_fi + timedelta(hours=24) if t_fi <= t_in else t_fi
                    
                    durata = (t_fi_eff - t_in).total_seconds() / 3600
                    # Continuato se copre 14:00-15:30
                    is_cont = (t_in <= timedelta(hours=14)) and (t_fi_eff >= timedelta(hours=15, minutes=30))

                    dati_righe.append({
                        "Data": giorno_corrente,
                        "Giorno": nomi_giorni[giorno_sett_idx],
                        "Giorno_Idx": giorno_sett_idx,
                        "Inizio": ora_in,
                        "Fine": ora_fi,
                        "Ore_Fatte": durata,
                        "Is_Cont": is_cont
                    })

    if not dati_righe: return pd.DataFrame(), 0, 0

    df = pd.DataFrame(dati_righe)
    # Aggregazione giornaliera
    df_daily = df.groupby('Data').agg({'Ore_Fatte':'sum', 'Giorno_Idx':'first', 'Is_Cont':'any'}).reset_index()
    
    def calcola_std(r):
        b = ore_standard_base[r['Giorno_Idx']]
        return b + 0.5 if (r['Giorno_Idx'] <= 3 and r['Is_Cont']) else b

    df_daily['Std'] = df_daily.apply(calcola_std, axis=1)
    df_daily['Straord'] = (df_daily['Ore_Fatte'] - df_daily['Std']).clip(lower=0)

    # Merge finale
    df_fin = df.merge(df_daily[['Data', 'Std', 'Straord', 'Is_Cont']], on='Data')
    
    tot_ore = df_daily['Ore_Fatte'].sum()
    tot_st = df_daily['Straord'].sum()

    # Formattazione colonne
    df_fin['Durata Turno'] = df_fin['Ore_Fatte'].apply(formatta_hhmm)
    df_fin['Straord. Giorno'] = df_fin['Straord'].apply(formatta_hhmm)
    df_fin['Standard Rif.'] = df_fin['Std'].apply(formatta_hhmm)
    df_fin['Orario Continuato'] = df_fin['Is_Cont'].map({True:'SI', False:'NO'})

    return df_fin[['Data', 'Giorno', 'Inizio', 'Fine', 'Durata Turno', 'Orario Continuato', 'Standard Rif.', 'Straord. Giorno']], tot_ore, tot_st

# --- UI ---
files = st.file_uploader("Carica PDF Perseo3", type="pdf", accept_multiple_files=True)

if files:
    all_ore, all_st = 0, 0
    fogli = []
    
    for f in files:
        df, ore, st_ore = analizza_pdf_perseo(f)
        if not df.empty:
            fogli.append((f.name[:30], df))
            all_ore += ore
            all_st += st_ore
        else:
            st.error(f"Impossibile leggere i dati da: {f.name}")

    if fogli:
        st.success("Analisi completata!")
        c1, c2 = st.columns(2)
        c1.metric("TOTALE ORE", formatta_hhmm(all_ore))
        c2.metric("TOTALE STRAORDINARIO", formatta_hhmm(all_st))

        out = io.BytesIO()
        with pd.ExcelWriter(out, engine='openpyxl') as writer:
            for nome, d in fogli:
                d.to_excel(writer, sheet_name=nome, index=False)
            pd.DataFrame({"Totale Ore":[formatta_hhmm(all_ore)], "Totale Straord":[formatta_hhmm(all_st)]}).to_excel(writer, sheet_name="RIEPILOGO")
        
        st.download_button("📥 Scarica Report Excel", out.getvalue(), "Report.xlsx")
