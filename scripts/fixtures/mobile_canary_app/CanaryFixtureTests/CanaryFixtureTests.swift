import XCTest

final class CanaryFixtureTests: XCTestCase {
    func testArithmeticSanity() {
        XCTAssertEqual(2 + 2, 4)
    }

    func testCanaryLabelExists() {
        let app = XCUIApplication()
        app.launch()
        let label = app.staticTexts["canary-label"]
        XCTAssertTrue(label.waitForExistence(timeout: 10),
                       "canary-label element not found on screen")
    }
}
