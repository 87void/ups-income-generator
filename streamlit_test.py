import streamlit as st

st.set_page_config(page_title="Streamlit Test")

st.title("Streamlit is working!")
st.write("The dashboard rendering test succeeded.")

if st.button("Test Button"):
    st.success("Button click handled successfully!")