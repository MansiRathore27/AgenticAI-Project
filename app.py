import re
import streamlit as st
from agents import run_agent

st.set_page_config(page_title="GovAssist AI", page_icon="🇮🇳", layout="wide")

# Consistent dark UI styling. Explicitly style Streamlit metric containers because
# the default theme can render white cards with white/low-contrast text.
st.markdown("""
<style>
:root { color-scheme: dark; }
.block-container {max-width: 1380px; padding-top: 1.15rem; padding-bottom: 5rem;}
.hero {padding: 1.35rem 1.55rem; border-radius: 16px;
       background: linear-gradient(120deg,#30233f,#1d3541 58%,#253044);
       color: #ffffff !important; border: 1px solid #596477; margin-bottom: 1rem;}
.hero h1, .hero p, .hero b, .hero span {color: #ffffff !important;}
.hero h1 {margin: 0 0 .35rem 0; font-size: 2.1rem;}
.hero p {margin-bottom: .25rem;}
.note {font-size: .9rem; opacity: .88;}
[data-testid="stMetric"], [data-testid="stMetricContainer"] {
  background: #20232d !important; color: #f5f7fb !important;
  border: 1px solid #454b5b !important; padding: 10px 12px !important;
  border-radius: 12px !important; min-height: 82px;
}
[data-testid="stMetricLabel"], [data-testid="stMetricLabel"] p,
[data-testid="stMetricValue"], [data-testid="stMetricValue"] div,
[data-testid="stMetricDelta"] {color: #f5f7fb !important;}
[data-testid="stMetricValue"] {font-size: 1.65rem !important;}
.stChatMessage {border-radius: 12px;}
[data-testid="stSidebar"] {border-right: 1px solid #373b48;}
[data-testid="stSidebar"] [data-testid="stWidgetLabel"] {font-weight: 600;}
.source-card {border: 1px solid #454b5b; border-radius: 10px; padding: .75rem; margin: .4rem 0;}
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="hero">
<h1>🇮🇳 GovAssist AI</h1>
<p><b>Agentic Government Benefits &amp; Application Assistant</b></p>
<p class="note">Plans the task → searches government sources → checks evidence quality → retries when needed → explains what is and is not verified.</p>
</div>
""", unsafe_allow_html=True)

STATES_UTS = [
    "Not specified", "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar",
    "Chhattisgarh", "Goa", "Gujarat", "Haryana", "Himachal Pradesh",
    "Jharkhand", "Karnataka", "Kerala", "Madhya Pradesh", "Maharashtra",
    "Manipur", "Meghalaya", "Mizoram", "Nagaland", "Odisha", "Punjab",
    "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana", "Tripura",
    "Uttar Pradesh", "Uttarakhand", "West Bengal", "Andaman and Nicobar Islands",
    "Chandigarh", "Dadra and Nagar Haveli and Daman and Diu", "Delhi",
    "Jammu and Kashmir", "Ladakh", "Lakshadweep", "Puducherry"
]
EDUCATION_OPTIONS = [
    "Not specified", "Below Class 10", "Class 10", "Class 11", "Class 12",
    "ITI", "Diploma", "Undergraduate (Bachelor's degree)",
    "Postgraduate (Master's degree)", "Integrated M.Tech", "Integrated M.Sc.",
    "PhD / Doctoral", "Other"
]
CATEGORIES = ["Not specified", "General", "OBC", "SC", "ST", "EWS", "Other"]
GENDERS = ["Not specified", "Female", "Male", "Other", "Prefer not to say"]

if "messages" not in st.session_state:
    st.session_state.messages = []
if "profile" not in st.session_state:
    st.session_state.profile = {}
if "last_result" not in st.session_state:
    st.session_state.last_result = None
if "pending_prompt" not in st.session_state:
    st.session_state.pending_prompt = None

with st.sidebar:
    st.header("👤 Student profile")
    st.caption("Optional. Select only details you want the assistant to use. Unspecified fields remain unknown.")
    old = st.session_state.profile

    age = st.number_input("Age (optional)", min_value=0, max_value=120,
                          value=int(old.get("age") or 0), step=1)
    saved_state = old.get("state", "Not specified")
    if saved_state not in STATES_UTS:
        saved_state = "Other"
    state = st.selectbox(
        "State / Union Territory",
        STATES_UTS,
        index=STATES_UTS.index(saved_state),
        help="Used to prioritize state-specific government schemes. Choose Not specified if you prefer not to share."
    )

    saved_education = old.get("education_level") or old.get("education") or "Not specified"
    if saved_education not in EDUCATION_OPTIONS:
        saved_education = "Other"
    education_level = st.selectbox(
        "Education level",
        EDUCATION_OPTIONS,
        index=EDUCATION_OPTIONS.index(saved_education),
        help="Choose your current education level. Add your exact course in the field below."
    )

    saved_course = old.get("course", "")
    course = st.text_input(
        "Course / specialization (optional)",
        value=str(saved_course),
        placeholder="e.g., AI and Data Science"
    )
    income = st.number_input("Annual family income in ₹ (optional; 0 = unknown)",
                              min_value=0, value=int(old.get("annual_income") or 0),
                              step=10000)
    saved_category = old.get("category", "Not specified")
    category = st.selectbox("Category (optional)", CATEGORIES,
                            index=CATEGORIES.index(saved_category) if saved_category in CATEGORIES else 0)
    saved_gender = old.get("gender", "Not specified")
    gender = st.selectbox("Gender (optional)", GENDERS,
                          index=GENDERS.index(saved_gender) if saved_gender in GENDERS else 0)
    domicile_options = ["Not specified", "Yes", "No", "In process"]
    saved_domicile = old.get("domicile_certificate", "Not specified")
    domicile_certificate = st.selectbox(
        "State domicile certificate? (optional)", domicile_options,
        index=domicile_options.index(saved_domicile) if saved_domicile in domicile_options else 0
    )

    if st.button("Save profile", type="primary", use_container_width=True):
        profile = {}
        if age:
            profile["age"] = int(age)
        if state != "Not specified":
            profile["state"] = state
        if education_level != "Not specified":
            profile["education_level"] = education_level
            profile["education"] = education_level
        if course.strip():
            profile["course"] = course.strip()
        if income:
            profile["annual_income"] = int(income)
        if category != "Not specified":
            profile["category"] = category
        if gender != "Not specified":
            profile["gender"] = gender
        if domicile_certificate != "Not specified":
            profile["domicile_certificate"] = domicile_certificate
        st.session_state.profile = profile
        st.success("Profile saved for this session.")

    st.divider()
    st.markdown("**Example questions**")
    examples = [
        "Find relevant government scholarships for my education and state. Check eligibility using official sources.",
        "Find government scholarships for college students in Madhya Pradesh.",
        "What information is missing to assess my scholarship eligibility?",
    ]
    for i, example in enumerate(examples):
        if st.button(example, key=f"example_{i}", use_container_width=True):
            st.session_state.pending_prompt = example

    if st.button("Clear chat and results", use_container_width=True):
        st.session_state.messages = []
        st.session_state.last_result = None
        st.session_state.pending_prompt = None
        st.rerun()

# Show conversation history.
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

prompt = st.chat_input("Describe your situation or ask about a government benefit...")
if st.session_state.pending_prompt:
    prompt = st.session_state.pending_prompt
    st.session_state.pending_prompt = None

if prompt:
    # Add the saved profile context to the query context without changing the user's message.
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)
    with st.chat_message("assistant"):
        try:
            with st.spinner("Planning, searching official sources, fetching pages, and reviewing evidence..."):
                result = run_agent(
                    prompt,
                    user_profile=st.session_state.profile,
                    conversation_history=st.session_state.messages[:-1],
                )
            st.session_state.profile = result.get("user_profile", st.session_state.profile)
            answer = result.get("final_answer") or "I could not generate an answer. Please try again."
            # Make HTML line breaks from model-generated markdown tables readable in Streamlit.
            answer = re.sub(r"(?i)(?:&lt;|<)br\s*/?(?:&gt;|>)", "\n", answer)
            answer = re.sub(r"(?i)&amp;lt;br\s*/?&amp;gt;", "\n", answer)
            answer = re.sub(r"[ \t]+\n", "\n", answer)
            st.markdown(answer)
            st.session_state.last_result = result
            st.session_state.messages.append({"role": "assistant", "content": answer})
        except Exception as exc:
            answer = f"The agent run failed: `{exc}`\n\nCheck your `.env` file, API key, and installed requirements."
            st.error(answer)
            st.session_state.messages.append({"role": "assistant", "content": answer})

result = st.session_state.last_result
if result:
    # Keep diagnostics out of the main chat view; users can open them when needed.
    with st.expander("⚙️ View run details (optional)", expanded=False):
        c1, c2, c3 = st.columns(3)
        c1.metric("Evidence", f"{result.get('evidence_score', 0)}/100")
        c2.metric("Retries", str(result.get("retries", 0)))
        c3.metric("Sources", str(len(result.get("search_results", []))))
        st.caption("Evidence score measures retrieved-page quality; it does not confirm personal eligibility.")

        with st.expander("🔎 Agent workflow", expanded=False):
            trace = result.get("trace", [])
            if trace:
                for item in trace:
                    st.write("• " + str(item))
            else:
                st.info("No workflow trace was returned for this run.")
            if result.get("plan"):
                st.markdown("**Plan**")
                for item in result["plan"]:
                    st.write("• " + str(item))
            if result.get("errors"):
                st.warning("Search warnings")
                for item in result["errors"][-5:]:
                    st.write("• " + str(item))

        with st.expander("📚 Sources checked", expanded=False):
            sources = result.get("search_results", [])
            if not sources:
                st.info("No sources were retained in this run.")
            for idx, item in enumerate(sources):
                title = item.get("page_title") or item.get("title") or "Untitled page"
                status = item.get("page_status", "NOT_FETCHED")
                st.markdown(f"**{title}** · `{status}`")
                url = item.get("url", "")
                if url:
                    st.markdown(f"[Open source]({url})")
                if item.get("is_generic_portal"):
                    st.caption("General portal — not scheme-specific proof of eligibility.")
                elif item.get("is_scheme_page"):
                    st.caption("Possible scheme page — check the actual eligibility rules below.")
                if item.get("snippet"):
                    st.caption("Search preview: " + item["snippet"][:300])
                content = (item.get("page_content") or "").strip()
                if content:
                    with st.expander(f"View page text {idx + 1}", expanded=False):
                        st.write(content[:2500])
                elif item.get("fetch_error"):
                    st.caption("Could not read page: " + item["fetch_error"][:180])
                st.divider()

st.caption("Safety note: This assistant does not submit applications. Confirm eligibility, deadlines, and application instructions on the official scheme page before acting.")
