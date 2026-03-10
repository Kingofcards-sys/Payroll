import streamlit as st
import pandas as pd
import numpy as np
from datetime import datetime
from io import BytesIO

# Page config
st.set_page_config(page_title="TPay Payroll System", layout="wide")
st.title("📊 TPay Payroll Automation System")

# ============================================================================
# DATA SOURCE SELECTOR
# ============================================================================
col1, col2, col3 = st.columns([1, 2, 1])
with col2:
    data_source = st.radio(
        "🔍 Select Payroll Data Source",
        options=["Biometric Attendance", "Tanka Pay"],
        horizontal=True,
        help="Choose the type of attendance data you want to process"
    )

st.divider()

# ============================================================================
# HELPER FUNCTIONS - COMMON HOUR NORMALIZATION
# ============================================================================
def hhmm_to_minutes(hhmm):
    """Convert HH:MM format to total minutes"""
    if not isinstance(hhmm, str) or ":" not in hhmm:
        return 0
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)

def normalize_work_hours(actual_minutes, unit=60, grace=5, min_hours=1, max_hours=10):
    """Industry-style normalization using bucket + grace"""
    actual_minutes = min(actual_minutes, max_hours * unit)
    base_bucket = (actual_minutes // unit) * unit
    next_bucket = base_bucket + unit
    
    if next_bucket - actual_minutes <= grace:
        normalized_minutes = next_bucket
    else:
        normalized_minutes = base_bucket
    
    normalized_hours = normalized_minutes // unit
    
    if normalized_hours < min_hours:
        return 0
    
    return min(normalized_hours, max_hours)

def get_normalized_hours(hhmm_str):
    """Get normalized hours from HH:MM string"""
    mins = hhmm_to_minutes(hhmm_str)
    return normalize_work_hours(mins)

# ============================================================================
# HELPER FUNCTIONS - TANKA PAY PARSER
# ============================================================================
def parse_tanka_attendance(df):
    """Parse Tanka Pay format attendance data"""
    emp_cols = ["OrgEmpCode", "OrganizationUnit", "Designation", "Department"]
    date_cols = [c for c in df.columns if "/" in str(c)]
    
    # Melt the dataframe
    long_df = df.melt(
        id_vars=emp_cols,
        value_vars=date_cols,
        var_name="Date",
        value_name="Raw_Attendance"
    )
    
    # Parse attendance function for Tanka Pay (pipe-separated format)
    def parse_attendance(val):
        if pd.isna(val):
            return pd.Series([None, None, None, None])
        
        val = str(val)
        
        if val in ["AA", "WO"]:
            return pd.Series([val, None, None, None])
        
        parts = val.split("|")
        status = parts[0]
        in_time, out_time, worked_hrs = None, None, None
        
        if len(parts) >= 2:
            try:
                time_range = parts[1].split("-")
                if len(time_range) == 2:
                    in_time = time_range[0].strip()
                    out_time = time_range[1].strip()
                    
                    if len(parts) >= 3:
                        worked_hrs = parts[2][:5]
                    elif in_time and out_time:
                        fmt = "%H:%M"
                        t1 = datetime.strptime(in_time, fmt)
                        t2 = datetime.strptime(out_time, fmt)
                        delta = t2 - t1
                        total_mins = int(delta.total_seconds() / 60)
                        if total_mins < 0:
                            total_mins += 1440
                        hh = total_mins // 60
                        mm = total_mins % 60
                        worked_hrs = f"{hh:02d}:{mm:02d}"
            except:
                pass
        
        return pd.Series([status, in_time, out_time, worked_hrs])
    
    # Apply parsing
    long_df[["Status", "In_Time", "Out_Time", "Worked_Hrs"]] = \
        long_df["Raw_Attendance"].apply(parse_attendance)
    
    final_df = long_df.drop(columns="Raw_Attendance")
    final_df["Date"] = pd.to_datetime(final_df["Date"], errors="coerce")
    
    # Normalize work hours
    final_df["Normalized_Work_Hrs"] = final_df["Worked_Hrs"].apply(get_normalized_hours)
    
    # Rename OrgEmpCode to employee_id for internal consistency
    final_df["employee_id"] = final_df["OrgEmpCode"]
    
    return final_df

# ============================================================================
# HELPER FUNCTIONS - BIOMETRIC PARSER
# ============================================================================
def parse_biometric_attendance(df):
    """Parse Biometric format attendance data (nested Excel structure)"""
    
    def calc_work_hours(in_t, out_t):
        """Calculate work hours from in and out times"""
        if not in_t or not out_t or ":" not in str(in_t) or ":" not in str(out_t):
            return "0:00"
        try:
            fmt = "%H:%M:%S" if str(in_t).count(":") == 2 else "%H:%M"
            t1 = datetime.strptime(str(in_t), fmt)
            t2 = datetime.strptime(str(out_t), fmt)
            diff = t2 - t1
            sec = diff.total_seconds()
            if sec < 0:
                sec += 86400
            return f"{int(sec//3600)}:{int((sec%3600)//60):02d}"
        except Exception as e:
            return "0:00"
    
    records = []
    current_emp_id = None
    current_emp_name = None
    
    # Extract year and month from data or use current
    year = datetime.now().year
    month = datetime.now().month
    
    for idx, row in df.iterrows():
        row_str = " ".join(row.astype(str))
        
        # Capture Employee Details
        if "Emp Code :" in row_str:
            for val in row:
                text = str(val)
                if "Emp Code :" in text:
                    current_emp_id = text.split(":")[1].strip()
                if "Emp Name :" in text:
                    current_emp_name = text.split(":")[1].strip()
            continue
        
        # Identify the row containing attendance data
        if isinstance(row.iloc[0], str) and "Shift" in row.iloc[0]:
            for day_num in range(1, len(df.columns)):
                cell_val = row.iloc[day_num]
                if pd.notna(cell_val) and "\n" in str(cell_val):
                    parts = str(cell_val).split("\n")
                    if len(parts) >= 8:
                        try:
                            date_obj = datetime(year, month, day_num)
                            in_t = parts[1].strip()
                            out_t = parts[2].strip()
                            
                            records.append({
                                "employee_id": current_emp_id,
                                "emp_id": current_emp_id,
                                "emp_name": current_emp_name,
                                "date": date_obj.strftime("%Y-%m-%d"),
                                "day_name": date_obj.strftime("%A"),
                                "in_time": in_t,
                                "out_time": out_t,
                                "work_hours": parts[6].strip(),
                                "calc_hrs": calc_work_hours(in_t, out_t),
                                "status": parts[7].strip()
                            })
                        except Exception:
                            continue
    
    attendance_df = pd.DataFrame(records)
    
    if len(attendance_df) == 0:
        return attendance_df
    
    # Normalize work hours
    attendance_df['Normalized_Work_Hrs'] = attendance_df['calc_hrs'].apply(get_normalized_hours)
    
    return attendance_df

# ============================================================================
# SECTION 1: ATTENDANCE FILE UPLOAD & PROCESSING
# ============================================================================
st.header("Section 1: Attendance File Processing")

if data_source == "Tanka Pay":
    st.subheader("📋 Tanka Pay Format")
    st.caption("Expected format: OrgEmpCode, dates as columns with pipe-separated values (Status|In_Time-Out_Time|Worked_Hrs)")
    
    attendance_file = st.file_uploader(
        "📁 Upload Tanka Pay Attendance File (.xlsx)",
        type=["xlsx"],
        key="attendance_upload_tanka"
    )
    
    if attendance_file:
        st.info("✓ Attendance file uploaded")
        
        try:
            df = pd.read_excel(attendance_file)
            final_df = parse_tanka_attendance(df)
            
            st.subheader("Processed Attendance Data Preview")
            st.dataframe(final_df.head(10), use_container_width=True)
            
            # Download button for cleaned attendance
            cleaned_csv = final_df.to_csv(index=False)
            st.download_button(
                label="📥 Download Cleaned Attendance File",
                data=cleaned_csv,
                file_name="cleaned_attendance_tanka.csv",
                mime="text/csv",
                key="download_cleaned_tanka"
            )
            
            # Store in session state
            st.session_state.cleaned_attendance = final_df
            st.session_state.data_source = "tanka"
            
        except Exception as e:
            st.error(f"❌ Error processing file: {str(e)}")

else:  # Biometric
    st.subheader("🔍 Biometric Format")
    st.caption("Expected format: Nested Excel structure with Emp Code, Emp Name, and Shift rows with daily data")
    
    attendance_file = st.file_uploader(
        "📁 Upload Biometric Attendance File (.xlsx)",
        type=["xlsx"],
        key="attendance_upload_bio"
    )
    
    if attendance_file:
        st.info("✓ Attendance file uploaded")
        
        try:
            df = pd.read_excel(attendance_file)
            final_df = parse_biometric_attendance(df)
            
            if len(final_df) == 0:
                st.warning("⚠️ No attendance records found. Please check the file format.")
            else:
                st.subheader("Processed Attendance Data Preview")
                st.dataframe(final_df.head(10), use_container_width=True)
                
                # Download button for cleaned attendance
                cleaned_csv = final_df.to_csv(index=False)
                st.download_button(
                    label="📥 Download Cleaned Attendance File",
                    data=cleaned_csv,
                    file_name="cleaned_attendance_biometric.csv",
                    mime="text/csv",
                    key="download_cleaned_bio"
                )
                
                # Store in session state
                st.session_state.cleaned_attendance = final_df
                st.session_state.data_source = "biometric"
        
        except Exception as e:
            st.error(f"❌ Error processing file: {str(e)}")

st.divider()

# ============================================================================
# SECTION 2: PAYROLL CALCULATION
# ============================================================================
st.header("Section 2: Payroll Calculation")

col1, col2 = st.columns(2)

with col1:
    attendance_input = st.file_uploader(
        "📁 Upload Cleaned Attendance File (.csv or .xlsx)",
        type=["csv", "xlsx"],
        key="attendance_input"
    )

with col2:
    salary_input = st.file_uploader(
        "📁 Upload Salary Master File (.xlsx)",
        type=["xlsx"],
        key="salary_input"
    )

if attendance_input and salary_input:
    st.info("✓ Both files uploaded successfully")
    
    # Read files
    if attendance_input.name.endswith('.csv'):
        spd = pd.read_csv(attendance_input)
    else:
        spd = pd.read_excel(attendance_input)
    
    salarypd = pd.read_excel(salary_input)
    
    # Identify the employee ID column in both dataframes
    # Normalize: use 'employee_id' if available, otherwise look for OrgEmpCode or emp_id
    emp_id_col_spd = None
    if 'employee_id' in spd.columns:
        emp_id_col_spd = 'employee_id'
    elif 'OrgEmpCode' in spd.columns:
        emp_id_col_spd = 'OrgEmpCode'
    elif 'emp_id' in spd.columns:
        emp_id_col_spd = 'emp_id'
    else:
        st.error("❌ Could not find employee ID column in attendance file")
        st.stop()
    
    emp_id_col_salary = None
    if 'employee_id' in salarypd.columns:
        emp_id_col_salary = 'employee_id'
    elif 'OrgEmpCode' in salarypd.columns:
        emp_id_col_salary = 'OrgEmpCode'
    elif 'emp_id' in salarypd.columns:
        emp_id_col_salary = 'emp_id'
    else:
        st.error("❌ Could not find employee ID column in salary file")
        st.stop()
    
    # Normalize salary dataframe to have 'employee_id' column
    if emp_id_col_salary != 'employee_id':
        salarypd['employee_id'] = salarypd[emp_id_col_salary]
    
    # Group by employee
    grouped_df = (
        spd.groupby(emp_id_col_spd, as_index=False)
        .agg(
            total_worked_hours=("Normalized_Work_Hrs", "sum"),
            daily_worked_list=("Normalized_Work_Hrs", list)
        )
    )
    
    # Rename for consistency
    grouped_df['employee_id'] = grouped_df[emp_id_col_spd]
    grouped_df = grouped_df.drop(columns=[emp_id_col_spd])
    
    # Merge with salary data
    payroll_df = grouped_df.merge(
        salarypd,
        on="employee_id",
        how="left"
    )
    
    # Input parameters
    col1, col2 = st.columns(2)
    
    with col1:
        TOTAL_DAYS = st.number_input("Total Days in Month", min_value=1, max_value=31, value=30)
    
    with col2:
        WEEKOFF_AND_PAID_LEAVE = st.number_input(
            "Weekoff & Paid Leave Days",
            min_value=0,
            max_value=31,
            value=0
        )
    
    if st.button("🔄 Generate Payroll Report"):
        
        # Ensure column names are clean
        payroll_df.columns = payroll_df.columns.str.strip()
        
        # Add basic fields
        payroll_df["total_days"] = TOTAL_DAYS
        payroll_df["weekoff_paid_leave"] = WEEKOFF_AND_PAID_LEAVE
        
        # Calculate expected hours
        payroll_df["total_expected_hours"] = (
            (TOTAL_DAYS - WEEKOFF_AND_PAID_LEAVE) * 
            payroll_df.get("daily_working_hours", 8)
        )
        
        payroll_df.loc[payroll_df["total_expected_hours"] < 0, "total_expected_hours"] = 0
        
        # Get daily working hours
        daily_working_hours = payroll_df.get("daily_working_hours", 8)
        Total_hours = TOTAL_DAYS * daily_working_hours
        
        # Calculate salary per hour
        payroll_df["salary"] = payroll_df.get("salary", 0)
        payroll_df["salary_hour"] = payroll_df["salary"] / Total_hours
        
        # Base salary
        payroll_df["base_salary"] = payroll_df["total_worked_hours"] * payroll_df["salary_hour"]
        
        # Paid weekend
        payroll_df["Paid_Weekend"] = (
            WEEKOFF_AND_PAID_LEAVE * 
            (payroll_df["salary_hour"] * daily_working_hours)
        )
        
        # Leverage calculation
        def calculate_leverage(row):
            daily_list = row.get("daily_worked_list", [])
            assigned_hours = row.get("daily_working_hours", 8)
            
            if not isinstance(daily_list, list):
                daily_list = []
            
            eligible = [h for h in daily_list if 0 < h < assigned_hours]
            
            if not eligible:
                return pd.Series([0, 0])
            
            eligible.sort()
            selected = eligible[:3]
            n = len(selected)
            difference = (n * assigned_hours) - sum(selected)
            leverage_hours = min(max(difference, 0), 3)
            leverage_amount = leverage_hours * row["salary_hour"]
            
            return pd.Series([leverage_hours, leverage_amount])
        
        payroll_df[["leverage_hr", "leverage_amount"]] = payroll_df.apply(
            calculate_leverage,
            axis=1
        )
        
        # Net salary
        payroll_df["net_salary"] = (
            payroll_df["base_salary"] + 
            payroll_df["Paid_Weekend"] + 
            payroll_df["leverage_amount"]
        )
        
        # Deductions
        payroll_df["deduction"] = np.select(
            [
                payroll_df["salary"] <= 21000,
                (payroll_df["salary"] >= 21001) & (payroll_df["salary"] <= 24999),
                payroll_df["salary"] >= 25000
            ],
            [
                (payroll_df["net_salary"] * 0.0075),
                0,
                200
            ],
            default=0
        )
        
        # Final salary
        payroll_df["Final_salary"] = (
            payroll_df["net_salary"] - payroll_df["deduction"].round(2)
        )
        
        # Column order (use employee_id instead of OrgEmpCode)
        column_order = [
            'employee_id',
            'salary',
            'total_days',
            'weekoff_paid_leave',
            'daily_working_hours',
            'total_expected_hours',
            'daily_worked_list',
            'total_worked_hours',
            'salary_hour',
            'base_salary',
            'Paid_Weekend',
            'leverage_hr',
            'leverage_amount',
            'deduction',
            'net_salary',
            'Final_salary'
        ]
        
        existing_cols = [col for col in column_order if col in payroll_df.columns]
        payroll_report = payroll_df[existing_cols].copy()
        
        st.subheader("📋 Payroll Report Preview")
        st.dataframe(payroll_report, use_container_width=True)
        
        # Store in session state
        st.session_state.payroll_report = payroll_report
        
        st.success("✓ Payroll calculated successfully!")

st.divider()

# ============================================================================
# SECTION 3: DOWNLOAD PAYROLL OUTPUT
# ============================================================================
st.header("Section 3: Download Payroll Output")

if 'payroll_report' in st.session_state:
    payroll_report = st.session_state.payroll_report
    
    # Convert to Excel
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
        payroll_report.to_excel(writer, index=False, sheet_name='Payroll')
    
    buffer.seek(0)
    
    st.download_button(
        label="📥 Download Payroll Excel Report",
        data=buffer,
        file_name="payroll_report.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="download_payroll"
    )
    
    st.info(f"✓ Report ready with {len(payroll_report)} employees")
else:
    st.warning("⚠️ Complete Section 2 to generate payroll report")