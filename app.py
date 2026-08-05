import streamlit as st
import os
import uuid
import psycopg2
from databricks.sdk import WorkspaceClient
from datetime import datetime
import time

# Page configuration
st.set_page_config(
    page_title="Support Ticket System",
    page_icon="🎫",
    layout="wide"
)

# Initialize workspace client and connection
@st.cache_resource
def get_db_connection():
    """Create a connection to Lakebase Postgres"""
    try:
        w = WorkspaceClient()
        cred = w.database.generate_database_credential(
            request_id=str(uuid.uuid4()),
            instance_names=[os.environ["LAKEBASE_DB_PGDATABASE"]]
        )
        
        conn = psycopg2.connect(
            host=os.environ["LAKEBASE_DB_PGHOST"],
            database=os.environ["LAKEBASE_DB_PGDATABASE"],
            user=os.environ["LAKEBASE_DB_PGUSER"],
            port=os.environ.get("LAKEBASE_DB_PGPORT", 5432),
            password=cred.token,
            sslmode="require"
        )
        return conn
    except Exception as e:
        st.error(f"Failed to connect to database: {str(e)}")
        return None

def execute_query(query, params=None, fetch=True):
    """Execute a database query"""
    conn = get_db_connection()
    if not conn:
        return None
    
    try:
        with conn.cursor() as cur:
            cur.execute(query, params)
            if fetch:
                columns = [desc[0] for desc in cur.description]
                results = cur.fetchall()
                return [dict(zip(columns, row)) for row in results]
            else:
                conn.commit()
                return True
    except Exception as e:
        st.error(f"Database error: {str(e)}")
        conn.rollback()
        return None

def get_all_tickets(status_filter=None):
    """Fetch all tickets, optionally filtered by status"""
    query = """
        SELECT ticket_id, title, status, priority, created_by, assigned_to, 
               created_at, updated_at
        FROM tickets
    """
    if status_filter and status_filter != "All":
        query += " WHERE status = %s"
        return execute_query(query, (status_filter.lower(),))
    else:
        query += " ORDER BY created_at DESC"
        return execute_query(query)

def get_ticket_messages(ticket_id):
    """Fetch all messages for a specific ticket"""
    query = """
        SELECT message_id, ticket_id, message_text, author, is_internal, created_at
        FROM ticket_messages
        WHERE ticket_id = %s
        ORDER BY created_at ASC
    """
    return execute_query(query, (ticket_id,))

def create_ticket(title, status, priority, created_by, assigned_to=None):
    """Create a new support ticket"""
    query = """
        INSERT INTO tickets (title, status, priority, created_by, assigned_to)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING ticket_id
    """
    result = execute_query(query, (title, status, priority, created_by, assigned_to), fetch=True)
    return result[0]['ticket_id'] if result else None

def add_message(ticket_id, message_text, author, is_internal=False):
    """Add a message to a ticket"""
    query = """
        INSERT INTO ticket_messages (ticket_id, message_text, author, is_internal)
        VALUES (%s, %s, %s, %s)
    """
    return execute_query(query, (ticket_id, message_text, author, is_internal), fetch=False)

def update_ticket_status(ticket_id, new_status):
    """Update the status of a ticket"""
    query = """
        UPDATE tickets
        SET status = %s, updated_at = CURRENT_TIMESTAMP
        WHERE ticket_id = %s
    """
    return execute_query(query, (new_status, ticket_id), fetch=False)

def get_ticket_stats():
    """Get ticket statistics"""
    query = """
        SELECT 
            COUNT(*) as total_tickets,
            COUNT(CASE WHEN status = 'open' THEN 1 END) as open_tickets,
            COUNT(CASE WHEN status = 'in_progress' THEN 1 END) as in_progress_tickets,
            COUNT(CASE WHEN status = 'resolved' THEN 1 END) as resolved_tickets,
            COUNT(CASE WHEN status = 'closed' THEN 1 END) as closed_tickets
        FROM tickets
    """
    return execute_query(query)

# Sidebar navigation
st.sidebar.title("🎫 Support Ticket System")
page = st.sidebar.radio(
    "Navigation",
    ["Dashboard", "Create Ticket", "Ticket Details"]
)

# Dashboard Page
if page == "Dashboard":
    st.title("Support Ticket Dashboard")
    
    # Show statistics
    stats = get_ticket_stats()
    if stats:
        stat = stats[0]
        col1, col2, col3, col4, col5 = st.columns(5)
        col1.metric("Total Tickets", stat['total_tickets'])
        col2.metric("Open", stat['open_tickets'])
        col3.metric("In Progress", stat['in_progress_tickets'])
        col4.metric("Resolved", stat['resolved_tickets'])
        col5.metric("Closed", stat['closed_tickets'])
    
    st.markdown("---")
    
    # Filter by status
    status_filter = st.selectbox(
        "Filter by Status",
        ["All", "Open", "In Progress", "Resolved", "Closed"]
    )
    
    # Display tickets
    tickets = get_all_tickets(status_filter if status_filter != "All" else None)
    
    if tickets:
        st.subheader(f"Tickets ({len(tickets)})")
        
        for ticket in tickets:
            with st.expander(f"🎫 #{ticket['ticket_id']} - {ticket['title']}"):
                col1, col2, col3 = st.columns(3)
                
                with col1:
                    st.write(f"**Status:** {ticket['status'].replace('_', ' ').title()}")
                    st.write(f"**Priority:** {ticket['priority'].title()}")
                
                with col2:
                    st.write(f"**Created By:** {ticket['created_by']}")
                    st.write(f"**Assigned To:** {ticket['assigned_to'] or 'Unassigned'}")
                
                with col3:
                    st.write(f"**Created:** {ticket['created_at'].strftime('%Y-%m-%d %H:%M') if ticket['created_at'] else 'N/A'}")
                    st.write(f"**Updated:** {ticket['updated_at'].strftime('%Y-%m-%d %H:%M') if ticket['updated_at'] else 'N/A'}")
                
                # Quick actions
                col1, col2, col3 = st.columns([1, 1, 3])
                with col1:
                    if st.button("View Details", key=f"view_{ticket['ticket_id']}"):
                        st.session_state['selected_ticket_id'] = ticket['ticket_id']
                        st.rerun()
                
                with col2:
                    new_status = st.selectbox(
                        "Update Status",
                        ["open", "in_progress", "resolved", "closed"],
                        index=["open", "in_progress", "resolved", "closed"].index(ticket['status']),
                        key=f"status_{ticket['ticket_id']}"
                    )
                    if new_status != ticket['status']:
                        if st.button("Update", key=f"update_{ticket['ticket_id']}"):
                            if update_ticket_status(ticket['ticket_id'], new_status):
                                st.success(f"Status updated to {new_status}!")
                                time.sleep(1)
                                st.rerun()
    else:
        st.info("No tickets found.")

# Create Ticket Page
elif page == "Create Ticket":
    st.title("Create New Support Ticket")
    
    with st.form("create_ticket_form"):
        title = st.text_input("Ticket Title*", placeholder="Brief description of the issue")
        
        col1, col2 = st.columns(2)
        with col1:
            status = st.selectbox("Status*", ["open", "in_progress", "resolved", "closed"])
            priority = st.selectbox("Priority*", ["low", "medium", "high", "urgent"])
        
        with col2:
            created_by = st.text_input("Created By (Email)*", placeholder="your.email@company.com")
            assigned_to = st.text_input("Assign To (Email)", placeholder="agent.email@company.com")
        
        initial_message = st.text_area(
            "Initial Message*",
            placeholder="Describe the issue in detail...",
            height=150
        )
        
        submitted = st.form_submit_button("Create Ticket")
        
        if submitted:
            # Validation
            if not title or not created_by or not initial_message:
                st.error("Please fill in all required fields (marked with *)")
            elif "@" not in created_by:
                st.error("Please enter a valid email address for 'Created By'")
            else:
                # Create ticket
                ticket_id = create_ticket(
                    title=title,
                    status=status,
                    priority=priority,
                    created_by=created_by,
                    assigned_to=assigned_to if assigned_to else None
                )
                
                if ticket_id:
                    # Add initial message
                    if add_message(ticket_id, initial_message, created_by):
                        st.success(f"✅ Ticket #{ticket_id} created successfully!")
                        st.balloons()
                        time.sleep(2)
                        st.session_state['selected_ticket_id'] = ticket_id
                        st.rerun()
                    else:
                        st.error("Ticket created but failed to add initial message")
                else:
                    st.error("Failed to create ticket")

# Ticket Details Page
elif page == "Ticket Details":
    st.title("Ticket Details")
    
    # Check if a ticket is selected
    if 'selected_ticket_id' not in st.session_state:
        st.info("Please select a ticket from the Dashboard to view details.")
        if st.button("Go to Dashboard"):
            st.switch_page("app.py")
    else:
        ticket_id = st.session_state['selected_ticket_id']
        
        # Fetch ticket details
        tickets = execute_query(
            "SELECT * FROM tickets WHERE ticket_id = %s",
            (ticket_id,)
        )
        
        if not tickets:
            st.error(f"Ticket #{ticket_id} not found.")
            if st.button("Back to Dashboard"):
                del st.session_state['selected_ticket_id']
                st.rerun()
        else:
            ticket = tickets[0]
            
            # Display ticket header
            col1, col2 = st.columns([3, 1])
            with col1:
                st.header(f"🎫 Ticket #{ticket['ticket_id']}: {ticket['title']}")
            with col2:
                if st.button("← Back to Dashboard"):
                    del st.session_state['selected_ticket_id']
                    st.rerun()
            
            # Ticket info
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Status", ticket['status'].replace('_', ' ').title())
            col2.metric("Priority", ticket['priority'].title())
            col3.write(f"**Created By:** {ticket['created_by']}")
            col4.write(f"**Assigned To:** {ticket['assigned_to'] or 'Unassigned'}")
            
            st.markdown("---")
            
            # Update status section
            st.subheader("Update Ticket Status")
            col1, col2 = st.columns([2, 3])
            with col1:
                new_status = st.selectbox(
                    "Change Status",
                    ["open", "in_progress", "resolved", "closed"],
                    index=["open", "in_progress", "resolved", "closed"].index(ticket['status'])
                )
                if new_status != ticket['status']:
                    if st.button("Update Status"):
                        if update_ticket_status(ticket_id, new_status):
                            st.success(f"Status updated to {new_status}!")
                            time.sleep(1)
                            st.rerun()
            
            st.markdown("---")
            
            # Messages section
            st.subheader("Messages")
            messages = get_ticket_messages(ticket_id)
            
            if messages:
                for msg in messages:
                    with st.container():
                        col1, col2 = st.columns([4, 1])
                        with col1:
                            st.markdown(f"**{msg['author']}** {'🔒 (Internal)' if msg['is_internal'] else ''}")
                        with col2:
                            st.caption(msg['created_at'].strftime('%Y-%m-%d %H:%M') if msg['created_at'] else '')
                        
                        st.write(msg['message_text'])
                        st.markdown("---")
            else:
                st.info("No messages yet.")
            
            # Add message form
            st.subheader("Add Message")
            with st.form("add_message_form"):
                col1, col2 = st.columns([3, 1])
                with col1:
                    message_author = st.text_input(
                        "Your Email*",
                        placeholder="your.email@company.com"
                    )
                with col2:
                    is_internal = st.checkbox("Internal Message")
                
                message_text = st.text_area(
                    "Message*",
                    placeholder="Type your message here...",
                    height=100
                )
                
                submit_message = st.form_submit_button("Send Message")
                
                if submit_message:
                    if not message_author or not message_text:
                        st.error("Please fill in all required fields")
                    elif "@" not in message_author:
                        st.error("Please enter a valid email address")
                    else:
                        if add_message(ticket_id, message_text, message_author, is_internal):
                            st.success("✅ Message added successfully!")
                            time.sleep(1)
                            st.rerun()
                        else:
                            st.error("Failed to add message")

# Footer
st.sidebar.markdown("---")
st.sidebar.caption("Built with Databricks Apps + Lakebase")