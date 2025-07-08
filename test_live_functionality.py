import requests
import json

def test_live_meeting_functionality():
    """Test the live meeting functionality"""
    
    print("=== LIVE MEETING FUNCTIONALITY TEST ===")
    
    # Test 1: Check if server is running
    try:
        response = requests.get('http://localhost:5000/')
        print(f"✅ Server is running (Status: {response.status_code})")
    except Exception as e:
        print(f"❌ Server is not running: {e}")
        return
    
    # Test 2: Check live meeting page
    try:
        response = requests.get('http://localhost:5000/live')
        if response.status_code == 200:
            print("✅ Live meeting page accessible")
        else:
            print(f"❌ Live meeting page failed: {response.status_code}")
    except Exception as e:
        print(f"❌ Live meeting page error: {e}")
    
    # Test 3: Check current meetings in database
    try:
        response = requests.get('http://localhost:5000/debug/live-meetings')
        if response.status_code == 200:
            data = response.json()
            print(f"✅ Database accessible")
            print(f"   Total meetings: {data.get('total_meetings', 0)}")
            print(f"   Live meetings: {data.get('live_meetings_count', 0)}")
            
            # Show recent live meetings
            live_meetings = data.get('live_meetings', [])
            if live_meetings:
                print("\n📋 Recent Live Meetings:")
                for i, meeting in enumerate(live_meetings[:3]):  # Show last 3
                    print(f"   {i+1}. {meeting.get('filename', 'Unknown')}")
                    print(f"      Date: {meeting.get('timestamp', 'N/A')}")
                    print(f"      Transcript length: {len(meeting.get('transcript', ''))}")
                    print(f"      Summary length: {len(meeting.get('summary', ''))}")
            else:
                print("   No live meetings found")
        else:
            print(f"❌ Database check failed: {response.status_code}")
    except Exception as e:
        print(f"❌ Database check error: {e}")
    
    # Test 4: Check meeting history page
    try:
        response = requests.get('http://localhost:5000/meetings/history')
        if response.status_code == 200:
            print("✅ Meeting history page accessible")
        else:
            print(f"❌ Meeting history page failed: {response.status_code}")
    except Exception as e:
        print(f"❌ Meeting history page error: {e}")
    
    print("\n🎯 READY FOR LIVE MEETING TESTING!")
    print("1. Go to http://localhost:5000/live")
    print("2. Start recording and speak clearly for 10+ seconds")
    print("3. Stop recording to save the meeting")
    print("4. Check http://localhost:5000/meetings/history to see the saved meeting")
    print("5. The meeting should appear with a 'Live Recording' badge")

if __name__ == "__main__":
    test_live_meeting_functionality() 