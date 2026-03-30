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
    if pd.isna(ore_decimali): return "00:00"
    ore = int(ore_decimali)
    minuti = int(round((ore_decimali - ore) * 60))
    return f"{ore:02d}:{minuti:02d}"

def analizza_pdf_perseo(pdf_file):
    # Standard: Lun-Gio 8h, Ven 4h
    ore_standard_base = {0: 8.0, 1: 8.0, 2: 8.0, 3: 8.0, 4: 4.0, 5: 0.0, 6: 0.0}
    nomi_giorni = ["Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"]
    
    turni_estratti = []
    giorno_corrente = "Sconosciuto"
    giorno_sett_idx = None

    with pdfplumber.open(pdf_file) as pdf:
        for pagina in pdf.pages:
            testo = pagina.extract_text()
            if not testo: continue
            
            # Pulizia per gestire righe spezzate o date multiple (tipico di Perseo3)
            linee = testo.replace('\r', '').split('\n')
            
            for linea in linee:
                # Cerca data (es: 01 Mer o 01/10/2025)
                match_data = re.search(r'(\d{2}\s+(?:Lun|Mar|Mer|Gio|Gia|Ven|Sab|Sah|Dom)|(\d{2}/\d{2}/\d{4}))', linea, re.IGNORECASE)
                if match_data:
                    giorno_corrente = match_data.group(1).strip()
                    low_g = giorno_corrente.lower()
                    if 'lun' in low_g: giorno_sett_idx = 0
                    elif 'mar' in low_g: giorno_sett_idx = 1
                    elif 'mer' in low_g: giorno_sett_idx = 2
                    elif 'gio' in low_g or 'gia' in low_g: giorno_sett_idx = 3
                    elif 'ven' in low_g: giorno_sett_idx = 4
                    elif 'sab' in low_g or 'sah' in low_g: giorno_sett_idx = 5
                    elif 'dom' in low_g: giorno_sett_idx = 6

                # Cerca orari HH:MM
                orari = re.findall(r'\b\d{2}:\d{2}\b', linea)
                for i in range(0, len(orari) // 2 * 2, 2):
                    ora_in, ora_fi = orari[i], orari[i+1]
                    if ora_in == "00:00" and ora_fi == "00:00": continue

                    t_in = converti_in_timedelta(ora_in)
                    t_fi = converti_in_timedelta(ora_fi)
                    t_fi_eff = t_fi + timedelta(hours=24) if t_fi <= t_in else t_fi
                    durata = (t_fi_eff - t_in).total_seconds() / 3600
                    
                    # Orario continuato se copre la fascia 14:00 - 15:30
                    is_cont = (t_in <= timedelta(hours=14)) and (t_fi_eff >= timedelta(hours=15, minutes=30))

                    turni_estratti.append({
                        "Data": giorno_corrente,
                        "Giorno_Idx": giorno_sett_idx,
                        "Giorno": nomi_giorni[giorno_sett_idx] if giorno_sett_idx is not None else "Sconosciuto",
                        "Inizio": ora_in,
                        "Fine": ora_fi,
                        "Ore_Fatte": durata,
                        "Is_Cont": is_cont
                    })

    if not turni_estratti:
        return pd.DataFrame(), 0, 0

    df = pd.DataFrame(turni_estratti)

    # CALCOLO GIORNALIERO (gestisce turni multipli sullo stesso giorno)
    df_totali = df.groupby('Data').agg({
        'Ore_Fatte': 'sum',
        'Is_Cont': 'any',
        'Giorno_Idx': 'first'
    }).reset_index()

    def calcola_std(row):
        if row['Giorno_Idx'] is None: return 0.0
        base = ore_standard_base.get(row['Giorno_Idx'], 0.0)
        # Se Lun-Gio ed è continuato, aggiunge 30 min (8.5 ore)
        if row['Giorno_Idx'] <= 3 and row['Is_Cont']:
            return base + 0.5
        return base

    df_totali['Standard_Giorno'] = df_totali.apply(calcola_std, axis=1)
    df_totali['Straord_Giorno_Dec'] = (df_totali['Ore_Fatte'] - df_totali['Standard_Giorno']).clip(lower=0)

    # Uniamo i totali alle righe dei turni
    df_finale = df.merge(df_totali[['Data', 'Standard_Giorno', 'Straord_Giorno_Dec', 'Is_Cont']], on='Data', how='left')

    # Pulizia nomi e formattazione
    df_finale['Durata Turno'] = df_finale['Ore_Fatte'].apply(formatta_hhmm)
    df_finale['Orario Continuato'] = df_finale['Is_Cont'].map({True: 'SI', False: 'NO'})
    df_finale['Standard Rif.'] = df_finale['Standard_Giorno'].apply(formatta_hhmm)
    df_finale['Straord. Giorno'] = df_finale['Straord_Giorno_Dec'].apply(formatta_hhmm)

    colonne = ['Data', 'Giorno', 'Inizio', 'Fine', 'Durata Turno', 'Orario Continuato', 'Standard Rif.', 'Straord. Giorno']
    
    return df_finale[colonne], df_totali['Ore_Fatte'].sum(), df_totali['Straord_Giorno_Dec'].sum()

# --- INTERFACCIA ---
files = st.file_uploader("Carica PDF Perseo3", type="pdf", accept_multiple_files=True)

if files:
    ore_tot, st_tot = 0, 0
    lista_fogli = []

    for f in files:
        df_m, o_m, s_m = analizza_pdf_perseo(f)
        if not df_m.empty:
            lista_fogli.append((f.name[:30], df_m))
            ore_tot += o_m
            st_tot += s_m

    if lista_fogli:
        st.divider()
        st.subheader("📈 RIEPILOGO GENERALE")
        c1, c2 = st.columns(2)
        c1.metric("TOTALE ORE LAVORATE", formatta_hhmm(ore_tot))
        c2.metric("TOTALE STRAORDINARIO", formatta_hhmm(st_tot))
        st.divider()

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            for nome, df in lista_fogli:
                df.to_excel(writer, sheet_name=nome, index=False)
                with st.expander(f"Dettaglio {nome}"):
                    st.dataframe(df)
            
            # Foglio riepilogativo finale
            pd.DataFrame({
                "DESCRIZIONE": ["TOTALE ORE", "TOTALE STRAORDINARIO"],
                "VALORE": [formatta_hhmm(ore_tot), formatta_hhmm(st_tot)]
            }).to_excel(writer, sheet_name="RIEPILOGO_TOTALE", index=False)

        st.download_button("📥 Scarica Report Excel", output.getvalue(), "Report_Ore_Lavoro.xlsx")
    else:
        st.error("Nessun dato trovato nei PDF caricati. Verifica che siano PDF originali Perseo3.")
