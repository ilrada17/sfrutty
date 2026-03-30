import streamlit as st
import pdfplumber
import re
import pandas as pd
import io
from datetime import datetime, timedelta

# Configurazione Pagina
st.set_page_config(page_title="Calcolatore Ore Lavoro", layout="wide")
st.title("📊 Calcolatore Ore e Straordinari")
st.write("Versione ottimizzata per turni multipli e giorni su più righe.")

def converti_in_timedelta(ora_str):
    if ora_str == "24:00": return timedelta(hours=24)
    try:
        ore, minuti = map(int, ora_str.split(':'))
        return timedelta(hours=ore, minutes=minuti)
    except: return timedelta(0)

def ricava_giorno_settimana(data_str):
    data_str = data_str.lower()
    if 'lun' in data_str: return 0
    if 'mar' in data_str: return 1
    if 'mer' in data_str: return 2
    if 'gio' in data_str or 'gia' in data_str: return 3
    if 'ven' in data_str: return 4
    if 'sab' in data_str or 'sah' in data_str: return 5
    if 'dom' in data_str: return 6
    match = re.search(r'(\d{2}/\d{2}/\d{4})', data_str)
    if match:
        try: return datetime.strptime(match.group(1), "%d/%m/%Y").weekday()
        except: return None
    return None

def formatta_hhmm(ore_decimali):
    """Formatta ore decimali in HH:MM (anche oltre le 24 ore)"""
    ore = int(ore_decimali)
    minuti = int(round((ore_decimali - ore) * 60))
    return f"{ore:02d}:{minuti:02d}"

def analizza_pdf(pdf_file):
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
    
    if not dati_righe: return pd.DataFrame(), 0, 0
    
    df = pd.DataFrame(dati_righe)
    
    # Raggruppamento per Data per calcolare il totale del giorno (somma i turni multipli)
    df_daily = df.groupby('Data').agg({
        'Ore_Fatte': 'sum',
        'Giorno_Idx': 'first',
        'Is_Cont': 'any'
    }).reset_index()
    
    df_daily = df_daily.rename(columns={'Ore_Fatte': 'Ore_Tot_Giorno', 'Is_Cont': 'Giorno_Continuato'})

    def calcola_standard(row):
        base = ore_standard_base[row['Giorno_Idx']]
        if row['Giorno_Idx'] <= 3 and row['Giorno_Continuato']: return base + 0.5
        return base

    df_daily['Standard_Applicato'] = df_daily.apply(calcola_standard, axis=1)
    df_daily['Straordinario_Dec'] = (df_daily['Ore_Tot_Giorno'] - df_daily['Standard_Applicato']).clip(lower=0)
    
    df_output = df.merge(df_daily[['Data', 'Standard_Applicato', 'Straordinario_Dec', 'Giorno_Continuato']], on='Data', how='left')
    
    # Totali complessivi per il file
    tot_ore_file = df_daily['Ore_Tot_Giorno'].sum()
    tot_straord_file = df_daily['Straordinario_Dec'].sum()

    df_output['Durata Turno'] = df_output['Ore_Fatte'].apply(formatta_hhmm)
    df_output['Straord. Giorno'] = df_output['Straordinario_Dec'].apply(formatta_hhmm)
    df_output['Standard Rif.'] = df_output['Standard_Applicato'].apply(formatta_hhmm)
    df_output['Diritto al Pasto'] = df_output['Giorno_Continuato'].map({True: 'SI', False: 'NO'})
    
    colonne_finali = ['Data', 'Giorno', 'Inizio', 'Fine', 'Durata Turno', 'Diritto al Pasto', 'Standard Rif.', 'Straord. Giorno']
    return df_output[colonne_finali], tot_ore_file, tot_straord_file

# --- INTERFACCIA STREAMLIT ---
uploaded_files = st.file_uploader("Carica i PDF Perseo3", type="pdf", accept_multiple_files=True)

if uploaded_files:
    fogli_da_salvare = []
    totale_generale_ore = 0
    totale_generale_straord = 0

    for uploaded_file in uploaded_files:
        df_mese, ore_file, straord_file = analizza_pdf(uploaded_file)
        if not df_mese.empty:
            fogli_da_salvare.append((uploaded_file.name[:31], df_mese))
            totale_generale_ore += ore_file
            totale_generale_straord += straord_file

    if fogli_da_salvare:
        st.divider()
        st.subheader("📈 Riepilogo Complessivo")
        c1, c2 = st.columns(2)
        c1.metric("Totale Ore Lavorate", formatta_hhmm(totale_generale_ore))
        c2.metric("Totale Straordinario", formatta_hhmm(totale_generale_straord))
        st.divider()

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            for nome_foglio, df in fogli_da_salvare:
                df.to_excel(writer, sheet_name=nome_foglio, index=False)
                with st.expander(f"Anteprima: {nome_foglio}"):
                    st.dataframe(df)
            
            # Foglio di riepilogo
            df_riepilogo = pd.DataFrame({
                "Descrizione": ["Totale Ore Lavorate", "Totale Ore Straordinario"],
                "Valore (HH:MM)": [formatta_hhmm(totale_generale_ore), formatta_hhmm(totale_generale_straord)]
            })
            df_riepilogo.to_excel(writer, sheet_name="RIEPILOGO_FINALE", index=False)

        st.download_button("📥 Scarica Report Excel", output.getvalue(), "Report_Ore_Lavoro.xlsx")
